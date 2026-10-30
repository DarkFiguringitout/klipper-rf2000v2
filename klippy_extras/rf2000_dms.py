# Renkforce RF1000/RF2000/RF2000v2 strain gauge ("DMS") support
#
# The RF printers measure the force on the hotend with a strain gauge that is
# read by a TI ADS1100/ADS1110 16 bit delta-sigma ADC at I2C address 0x49.
# The original firmware uses it for:
#   * heat bed scan / Z-compensation (nozzle is the probe)
#   * emergency pause when the force gets too high while printing
#   * emergency stop when Z is blocked
#   * "SenseOffset" (M3909) and digit based flow/speed compensation (M3911)
#   * z-offset scan (M3900) and "find Z origin" for the milling mode
#
# This module implements all of that on top of mainline Klipper.  It
# registers itself as the printer "probe" object so that PROBE,
# PROBE_ACCURACY, PROBE_CALIBRATE, BED_MESH_CALIBRATE, SCREWS_TILT_CALCULATE,
# Z_TILT_ADJUST etc. all work with the strain gauge.
#
# Probing is done in two passes using toolhead.drip_move() (the same
# mechanism Klipper uses for homing) while a reactor timer polls the ADC:
#   1. fast descent until the force rises by `contact_delta` digits
#   2. back off, re-measure the idle force, then a slow descent
# The contact height is taken from the Z stepper position at the time the
# triggering ADC sample was taken, so the result does not depend on how far
# the toolhead travelled after the trigger.
#
# Copyright (C) 2026  klipper-rf2000v2 contributors
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import bus, probe, manual_probe

# Samples per second for the DR bits of the config register
ADC_RATES = {
    'ads1110': {0: 240., 1: 60., 2: 30., 3: 15.},
    'ads1100': {0: 128., 1: 32., 2: 16., 3: 8.},
}
DEFAULT_ADC_CONFIG = 0x8C  # continuous, 16 bit, PGA=1 (as RF firmware)


def config_getint_hex(config, option, default, minval, maxval):
    """Like config.getint() but also accepts hex (0x..) values."""
    raw = config.get(option, None)
    if raw is None:
        return default
    try:
        val = int(raw.strip(), 0)
    except ValueError:
        raise config.error("Unable to parse option '%s' in section '%s'"
                           % (option, config.get_name()))
    if not minval <= val <= maxval:
        raise config.error("Option '%s' in section '%s' must be between"
                           " 0x%x and 0x%x" % (option, config.get_name(),
                                               minval, maxval))
    return val


class ADS1110:
    """Minimal driver for the ADS1100/ADS1110 on the RF hotend."""
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.i2c = bus.MCU_I2C_from_config(config, default_addr=0x49,
                                           default_speed=100000)
        self.chip = config.getchoice('adc_type', {k: k for k in ADC_RATES},
                                     'ads1110')
        self.adc_config = config_getint_hex(config, 'adc_config',
                                            DEFAULT_ADC_CONFIG, 0, 0xff)
        self.sps = ADC_RATES[self.chip][(self.adc_config >> 2) & 0x3]
        self.use_drdy = config.getboolean('use_data_ready', True)
        self.invert = config.getboolean('invert', False)
        self.lock = self.reactor.mutex()
        self.last_value = None
        self.last_time = 0.
        self.drdy_seen = False
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
    def get_mcu(self):
        return self.i2c.get_mcu()
    def _handle_connect(self):
        self.i2c.i2c_write([self.adc_config])
    def get_sample_period(self):
        return 1. / self.sps
    def read(self):
        """Return (value, is_new_sample, host_time_of_request)."""
        if self.get_mcu().is_fileoutput():
            # Batch/simulation mode - no sensor responses available
            return 0, True, self.reactor.monotonic()
        with self.lock:
            params = self.i2c.i2c_read([], 3)
        resp = bytearray(params['response'])
        if len(resp) < 3:
            raise self.printer.command_error("DMS: short I2C read")
        raw = (resp[0] << 8) | resp[1]
        if raw & 0x8000:
            raw -= 0x10000
        if self.invert:
            raw = -raw
        fresh = not (resp[2] & 0x80)
        if fresh:
            self.drdy_seen = True
        if not self.use_drdy:
            fresh = True
        sent = params.get('#sent_time', self.reactor.monotonic())
        self.last_value = raw
        self.last_time = sent
        return raw, fresh, sent
    def read_fresh(self, timeout=None):
        if timeout is None:
            timeout = 3. * self.get_sample_period() + 0.25
        end = self.reactor.monotonic() + timeout
        while 1:
            val, fresh, t = self.read()
            if fresh:
                return val, t
            now = self.reactor.monotonic()
            if now > end:
                if not self.drdy_seen:
                    # Some ADC variants never clear the DRDY bit in
                    # continuous mode - fall back to timed sampling.
                    logging.info("rf2000_dms: DRDY never seen, using timed"
                                 " sampling")
                    self.use_drdy = False
                    return val, t
                raise self.printer.command_error(
                    "DMS: no new ADC sample (sensor stuck?)")
            self.reactor.pause(now + min(0.010,
                                         self.get_sample_period() / 4.))


class RF2000DMS:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        self.name = config.get_name()
        self.sensor = ADS1110(config)
        self.gcode = printer.lookup_object('gcode')
        # Tare ("zero digits")
        self.tare = 0
        # Probing configuration (defaults from RF2000v2 firmware HBS)
        self.z_offset = config.getfloat('z_offset', 0.)
        # Added to each z-offset scan result (the contact is measured at
        # 150 C, the print runs hotter and needs some squish room)
        self.scan_trim = config.getfloat('z_offset_scan_trim', 0.)
        self.x_offset = config.getfloat('x_offset', 0.)
        self.y_offset = config.getfloat('y_offset', 0.)
        self.contact_delta = config.getint('contact_delta', 10, minval=1)
        # RF fw averages 15 reads; require several consecutive new samples
        # beyond contact_delta so a single noise spike can not trigger
        self.contact_samples = config.getint('contact_samples', 2, minval=1,
                                             maxval=10)
        self.release_delta = config.getint('release_delta', 5, minval=1)
        self.idle_samples = config.getint('idle_samples', 5, minval=1)
        self.idle_tolerance = config.getint('idle_tolerance', 15, minval=1)
        self.idle_retries = config.getint('idle_retries', 5, minval=0)
        self.idle_min = config.getint('idle_min', -7500)
        self.idle_max = config.getint('idle_max', 7500)
        self.slow_speed = config.getfloat('slow_speed', 0.05, above=0.)
        self.backoff_dist = config.getfloat('backoff_distance', 0.15,
                                            above=0.)
        self.release_dist = config.getfloat('release_distance', 0.10,
                                            minval=0.)
        self.settle_time = config.getfloat('settle_time', 0.150, minval=0.)
        self.sample_delay = config.getfloat(
            'sample_delay', .5 * self.sensor.get_sample_period(), minval=0.)
        # Abort a probe if the force after the fast approach exceeds this
        # (too much overtravel - lower the probe speed)
        self.max_contact_digits = config.getint('max_contact_digits', 5000,
                                                minval=1)
        self.backoff_retries = config.getint('backoff_retries', 3, minval=0)
        # Contact must be rigid: pushing stiffness_push further must raise
        # the force by at least min_stiffness digits/mm. A nozzle on the bed
        # gives ~5000 digits/mm on a real RF2000v2 (~1000 at the flexing
        # front-left corner), a plastic blob 40-200.
        self.min_stiffness = config.getfloat('min_stiffness', 400., minval=0.)
        self.stiffness_push = config.getfloat('stiffness_push', 0.05,
                                              above=0., maxval=0.1)
        self.z_min_position = probe.lookup_minimum_z(config)
        self.param_helper = probe.ProbeParameterHelper(config)
        self.probe_session = probe.SampleAveragingHelper(
            config, self.param_helper, self._start_hw_session)
        self.cmd_helper = probe.ProbeCommandHelper(config, self,
                                                   self._query_contact)
        self.results = []
        self.probing = 0
        self.last_idle = None
        # Emergency pause (while printing)
        self.pause_min = config.getint('emergency_pause_digits_min', -15000)
        self.pause_max = config.getint('emergency_pause_digits_max', 15000)
        self.pause_checks = config.getint('emergency_pause_checks', 10,
                                          minval=1)
        gcode_macro = printer.load_object(config, 'gcode_macro')
        self.pause_gcode = gcode_macro.load_template(config, 'pause_gcode',
                                                     'PAUSE')
        # Emergency stop (Z blocked while moving Z without extrusion)
        self.stop_min = config.getint('emergency_stop_digits_min', -5000)
        self.stop_max = config.getint('emergency_stop_digits_max', 5000)
        self.stop_checks = config.getint('emergency_stop_checks', 2,
                                         minval=1)
        # Nozzle pressure stays high for seconds after extruding (measured:
        # 4700 digits 1 s and 1500 digits 3 s after stopping without a
        # retract), so a Z move right after extrusion is not a blocked bed.
        # A purge line followed by a Z lift tripped the stop at 7710 digits.
        self.stop_holdoff = config.getfloat('emergency_stop_holdoff', 3.0,
                                            minval=0.)
        self.last_extrude_time = -999.
        # faster polling while Z moves (RF fw checks every 10ms)
        self.stop_interval = config.getfloat('emergency_stop_interval', 0.050,
                                             minval=0.020)
        # identical ADC values in a row while descending = frozen sensor
        self.frozen_samples = config.getint('frozen_samples', 75, minval=5)
        # Monitor
        self.monitor_interval = config.getfloat('monitor_interval', 0.100,
                                                minval=0.020)
        self.monitor_enabled = config.getboolean('monitor', True)
        self.monitor_timer = None
        self.pause_acc = []
        self.stop_acc = []
        self.paused_by_dms = False
        # SenseOffset (M3909)
        self.so_max_digits = 0
        self.so_max_offset = config.getfloat('sense_offset_max', 0.180,
                                             minval=0., maxval=0.300)
        self.so_max_height = config.getfloat('sense_offset_max_height', 0.30,
                                             above=0.)
        self.so_step = config.getfloat('sense_offset_step', 0.002, above=0.)
        self.so_applied = 0.
        # Digit flow/speed compensation (M3911)
        self.fc_min = 0
        self.fc_max = 0
        self.fc_extrude = 0.
        self.fc_speed = 0.
        self.fc_min_height = config.getfloat('flow_compensation_min_height',
                                             0.30, minval=0.)
        self.fc_avg = None
        self.fc_last_e = 1.
        self.fc_last_s = 1.
        self.fc_base = None
        # Closed loop pressure control (PI controller on nozzle force)
        self.pc_target = config.getint('pressure_control_target', 0,
                                       minval=0)
        self.pc_kp = config.getfloat('pressure_control_kp', 0.5, minval=0.)
        self.pc_ki = config.getfloat('pressure_control_ki', 0.2, minval=0.)
        self.pc_min_speed = config.getfloat('pressure_control_min_speed',
                                            50., minval=5., maxval=100.)
        self.pc_min_flow = config.getfloat('pressure_control_min_flow',
                                           100., minval=50., maxval=100.)
        self.pc_mode = config.getchoice(
            'pressure_control_mode', {'speed': 'speed', 'flow': 'flow',
                                      'both': 'both'}, 'speed')
        self.pc_integral = 0.
        self.pc_avg = None
        self.pc_factor = 1.
        self.pc_base = None
        # Register as the printer probe
        printer.add_object('probe', self)
        printer.register_event_handler("klippy:ready", self._handle_ready)
        printer.register_event_handler("klippy:shutdown",
                                       self._handle_shutdown)
        # Commands
        gc = self.gcode
        gc.register_command('DMS_READ', self.cmd_DMS_READ,
                            desc=self.cmd_DMS_READ_help)
        gc.register_command('DMS_TARE', self.cmd_DMS_TARE,
                            desc=self.cmd_DMS_TARE_help)
        gc.register_command('DMS_TEST', self.cmd_DMS_TEST,
                            desc=self.cmd_DMS_TEST_help)
        gc.register_command('DMS_MONITOR', self.cmd_DMS_MONITOR,
                            desc=self.cmd_DMS_MONITOR_help)
        gc.register_command('DMS_SET_LIMITS', self.cmd_DMS_SET_LIMITS,
                            desc=self.cmd_DMS_SET_LIMITS_help)
        gc.register_command('DMS_Z_OFFSET_SCAN', self.cmd_DMS_Z_OFFSET_SCAN,
                            desc=self.cmd_DMS_Z_OFFSET_SCAN_help)
        gc.register_command('DMS_FIND_Z_ORIGIN', self.cmd_DMS_FIND_Z_ORIGIN,
                            desc=self.cmd_DMS_FIND_Z_ORIGIN_help)
        gc.register_command('DMS_SENSE_OFFSET', self.cmd_DMS_SENSE_OFFSET,
                            desc=self.cmd_DMS_SENSE_OFFSET_help)
        gc.register_command('DMS_FLOW_COMPENSATION',
                            self.cmd_DMS_FLOW_COMPENSATION,
                            desc=self.cmd_DMS_FLOW_COMPENSATION_help)
        gc.register_command('DMS_PRESSURE_CONTROL',
                            self.cmd_DMS_PRESSURE_CONTROL,
                            desc=self.cmd_DMS_PRESSURE_CONTROL_help)
        gc.register_command('DMS_VISCOSITY_TEST', self.cmd_DMS_VISCOSITY_TEST,
                            desc=self.cmd_DMS_VISCOSITY_TEST_help)
        gc.register_command('DMS_LOAD_FILAMENT', self.cmd_DMS_LOAD_FILAMENT,
                            desc=self.cmd_DMS_LOAD_FILAMENT_help)
        gc.register_command('DMS_UNLOAD_FILAMENT',
                            self.cmd_DMS_UNLOAD_FILAMENT,
                            desc=self.cmd_DMS_UNLOAD_FILAMENT_help)
        if config.getboolean('rf_gcode_aliases', True):
            # Compatibility with g-code written for the RF firmware
            gc.register_command('M3900', self.cmd_DMS_Z_OFFSET_SCAN,
                                desc="RF: z-offset scan (DMS_Z_OFFSET_SCAN)")
            gc.register_command('M3909', self.cmd_M3909,
                                desc="RF: SenseOffset (DMS_SENSE_OFFSET)")
            gc.register_command('M3911', self.cmd_M3911,
                                desc="RF: digit flow compensation")
            gc.register_command('M3913', self.cmd_M3913,
                                desc="RF: mount filament until force P")
            gc.register_command('M3914', self.cmd_M3914,
                                desc="RF: unmount filament (soft)")
            gc.register_command('M3939', self.cmd_M3939,
                                desc="RF: SensibleViscosity test")
    # Value helpers
    def read_value(self):
        val, fresh, t = self.sensor.read()
        return val - self.tare, fresh, t
    def read_fresh(self):
        val, t = self.sensor.read_fresh()
        return val - self.tare, t
    def _toolhead(self):
        return self.printer.lookup_object('toolhead')
    def _z_steppers(self):
        kin = self._toolhead().get_kinematics()
        return [s for s in kin.get_steppers() if s.is_active_axis('z')]
    # Probe interface
    def get_probe_params(self, gcmd=None):
        return self.param_helper.get_probe_params(gcmd)
    def get_offsets(self, gcmd=None):
        return self.x_offset, self.y_offset, self.z_offset
    def get_status(self, eventtime):
        status = self.cmd_helper.get_status(eventtime)
        status.update({
            'digits': self.sensor.last_value,
            'force': (None if self.sensor.last_value is None
                      else self.sensor.last_value - self.tare),
            'tare': self.tare,
            'idle': self.last_idle,
            'monitor': self.monitor_enabled,
            'probing': bool(self.probing),
            'sense_offset': self.so_applied,
            'sense_offset_digits': self.so_max_digits,
            'flow_compensation': [self.fc_min, self.fc_max, self.fc_extrude,
                                  self.fc_speed],
            'paused_by_dms': self.paused_by_dms,
            'pressure_control_target': self.pc_target,
            'pressure_control_factor': self.pc_factor,
            'pressure_avg': self.pc_avg,
        })
        return status
    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)
    def _start_hw_session(self, gcmd):
        self.results = []
        self.probing += 1
        return self
    def end_probe_session(self):
        self.results = []
        self.probing = max(0, self.probing - 1)
    def pull_probed_results(self):
        res = self.results
        self.results = []
        return res
    def _query_contact(self, print_time):
        idle = self.last_idle if self.last_idle is not None else 0
        val, t = self.read_fresh()
        return abs(val - idle) >= self.contact_delta
    # Probing implementation
    def measure_idle(self):
        # Queued moves must have finished: acceleration loads the hotend
        # holder (10-30 digits on a real RF2000v2), which made every probe
        # fail with "triggered prior to movement"
        self._toolhead().wait_moves()
        reactor = self.reactor
        reactor.pause(reactor.monotonic() + self.settle_time)
        for attempt in range(self.idle_retries + 1):
            vals = [self.read_fresh()[0] for i in range(self.idle_samples)]
            if max(vals) - min(vals) <= self.idle_tolerance:
                idle = sum(vals) / float(len(vals))
                if not self.idle_min <= idle <= self.idle_max:
                    raise self.printer.command_error(
                        "DMS idle pressure %.0f outside %d..%d - run DMS_TARE"
                        " with the nozzle free, or check the strain gauge"
                        % (idle, self.idle_min, self.idle_max))
                self.last_idle = idle
                return idle
            reactor.pause(reactor.monotonic() + self.settle_time)
        raise self.printer.command_error(
            "DMS idle pressure not stable (spread %d > %d digits)"
            % (max(vals) - min(vals), self.idle_tolerance))
    def _sample_z(self, host_time):
        zs = self._z_steppers()
        if not zs:
            raise self.printer.command_error("DMS: no Z stepper found")
        stepper = zs[0]
        print_time = (stepper.get_mcu().estimated_print_time(host_time)
                      - self.sample_delay)
        return stepper.mcu_to_commanded_position(
            stepper.get_past_mcu_position(print_time))
    def _resync_toolhead(self):
        toolhead = self._toolhead()
        toolhead.flush_step_generation()
        kin = toolhead.get_kinematics()
        kin_spos = {s.get_name(): s.get_commanded_position()
                    for s in kin.get_steppers()}
        cpos = kin.calc_position(kin_spos)
        thpos = toolhead.get_position()
        newpos = [cp if cp is not None else tp
                  for cp, tp in zip(cpos, thpos[:3])] + thpos[3:]
        toolhead.set_position(newpos)
        return newpos
    def descend_until_contact(self, speed, idle, delta, z_target=None):
        """Drip move down until |force-idle| >= delta.  Returns the Z
        height at the triggering sample, or None if no contact."""
        toolhead = self._toolhead()
        reactor = self.reactor
        if z_target is None:
            z_target = self.z_min_position
        toolhead.wait_moves()
        completion = reactor.completion()
        state = {'time': None, 'value': None, 'error': None, 'run': [],
                 'samples': 0, 'finished': False}
        poll_time = max(0.005, self.sensor.get_sample_period() / 4.)
        def poll(eventtime):
            if completion.test() or state['finished']:
                return reactor.NEVER
            try:
                val, fresh, t = self.read_value()
            except Exception as e:
                state['error'] = str(e)
                completion.complete(True)
                return reactor.NEVER
            if state['finished']:
                # the move ended while this read was in progress
                return reactor.NEVER
            if fresh:
                state['samples'] += 1
                if val == state.get('last') and self.sensor.use_drdy:
                    state['same'] = state.get('same', 0) + 1
                    if state['same'] >= self.frozen_samples:
                        state['error'] = (
                            "DMS: strain gauge value frozen at %d digits"
                            " during the probe move - check the sensor"
                            % (val,))
                        completion.complete(True)
                        return reactor.NEVER
                else:
                    state['same'] = 0
                state['last'] = val
                if abs(val - idle) >= delta:
                    state['run'].append((t, val))
                    if len(state['run']) >= self.contact_samples:
                        if state['samples'] == len(state['run']):
                            # beyond the threshold since the first sample
                            state['error'] = (
                                "DMS probe triggered prior to movement"
                                " (force %d, idle %.0f)" % (val, idle))
                            completion.complete(True)
                            return reactor.NEVER
                        # contact time = first sample of the run
                        state['time'], state['value'] = state['run'][0]
                        completion.complete(True)
                        return reactor.NEVER
                else:
                    state['run'] = []
            return eventtime + poll_time
        timer = reactor.register_timer(poll, reactor.NOW)
        pos = toolhead.get_position()
        if pos[2] <= z_target:
            reactor.unregister_timer(timer)
            raise self.printer.command_error(
                "DMS probe start position below minimum Z")
        pos[2] = z_target
        try:
            toolhead.drip_move(pos, speed, completion)
        finally:
            state['finished'] = True
            if not completion.test():
                completion.complete(False)
            reactor.unregister_timer(timer)
            self._resync_toolhead()
        if state['error'] is not None:
            raise self.printer.command_error(state['error'])
        if state['time'] is None:
            return None, None
        return self._sample_z(state['time']), state['value']
    def probe_contact(self, gcmd, contact_delta=None, check_stiffness=True):
        params = self.get_probe_params(gcmd)
        if contact_delta is None:
            contact_delta = gcmd.get_int('CONTACT_DELTA', self.contact_delta,
                                         minval=1)
        slow_speed = gcmd.get_float('SLOW_SPEED', self.slow_speed, above=0.)
        toolhead = self._toolhead()
        # Pass 1: fast approach
        idle = self.measure_idle()
        z1, v1 = self.descend_until_contact(params['probe_speed'], idle,
                                            contact_delta)
        if z1 is None:
            raise self.printer.command_error(
                "DMS: no contact detected down to Z=%.3f"
                % (self.z_min_position,))
        # Overtravel check: the flushed part of the drip move keeps running
        # after the trigger, which presses the nozzle into the bed
        peak, t = self.read_fresh()
        if abs(peak - idle) > self.max_contact_digits:
            pos = toolhead.get_position()
            pos[2] = max(pos[2], z1) + self.backoff_dist
            toolhead.manual_move(pos, params['lift_speed'])
            raise self.printer.command_error(
                "DMS: force %d digits after contact exceeds"
                " max_contact_digits %d - lower the probe speed"
                % (abs(peak - idle), self.max_contact_digits))
        # Back off until the force is released and re-measure idle force
        first_idle = idle
        pos = toolhead.get_position()
        pos[2] = max(pos[2], z1) + self.backoff_dist
        toolhead.manual_move(pos, params['lift_speed'])
        prev = None
        for attempt in range(self.backoff_retries + 1):
            toolhead.wait_moves()  # the lift is only queued until here
            val, t = self.read_fresh()
            if abs(val - first_idle) < self.release_delta + self.idle_tolerance:
                break
            # Free = lifting further no longer changes the force. The idle
            # value itself can drift 25-60 digits during a probe while bed
            # and nozzle heat (seen on a real RF2000v2), so the comparison
            # with the pre-contact idle alone failed probes at random.
            if prev is not None and abs(val - prev) < self.idle_tolerance:
                break
            prev = val
            pos[2] += self.backoff_dist
            toolhead.manual_move(pos, params['lift_speed'])
        else:
            raise self.printer.command_error(
                "DMS: force not released after back-off (%d digits)"
                % (val - first_idle,))
        idle = self.measure_idle()
        # Pass 2: slow approach
        z2, v2 = self.descend_until_contact(slow_speed, idle, contact_delta)
        if z2 is None:
            raise self.printer.command_error("DMS: no contact in slow pass")
        if check_stiffness and self.min_stiffness:
            toolhead.wait_moves()
            pos = toolhead.get_position()
            pos[2] -= self.stiffness_push
            toolhead.manual_move(pos, slow_speed)
            toolhead.wait_moves()
            self.read_fresh()  # this sample may still be from the move
            f2 = self.read_fresh()[0]
            # Total force over the total depth below the contact point: the
            # force right after the slow pass is still settling, and the
            # stiff-but-flexing bed corner (~1000 digits/mm at X5 Y5 on the
            # test printer) looked like a blob when measured from there.
            depth = max(z2 - pos[2], self.stiffness_push)
            stiffness = max(abs(f2 - idle) - contact_delta, 0.) / depth
            logging.info("rf2000_dms: stiffness check z2=%.4f push_to=%.4f"
                         " idle=%.0f f2=%.0f v2=%s -> %.0f digits/mm",
                         z2, pos[2], idle, f2, v2, stiffness)
            if stiffness < self.min_stiffness:
                pos[2] = max(pos[2], z2) + self.backoff_dist
                toolhead.manual_move(pos, params['lift_speed'])
                raise self.printer.command_error(
                    "DMS: soft contact (%.0f digits/mm, the nozzle on the bed"
                    " gives several 1000) - plastic on the nozzle? Clean it"
                    " and probe again" % (stiffness,))
        # Release the force on the nozzle
        if self.release_dist:
            pos = toolhead.get_position()
            pos[2] = max(pos[2], z2) + self.release_dist
            toolhead.manual_move(pos, params['lift_speed'])
        return z2, idle, v2
    def _probe_contact_retry(self, gcmd, contact_delta=None):
        # A false trigger (gauge zero shift) shows up as a "soft contact";
        # it is random, so one retry from the lifted position is safe. A
        # real blob fails both attempts.
        try:
            return self.probe_contact(gcmd, contact_delta)
        except self.printer.command_error as e:
            if 'soft contact' not in str(e):
                raise
            logging.info("rf2000_dms: %s - retrying once", e)
            gcmd.respond_info("DMS: soft contact, probing this point again")
            return self.probe_contact(gcmd, contact_delta)
    def run_probe(self, gcmd):
        toolhead = self._toolhead()
        start = toolhead.get_position()
        z, idle, val = self._probe_contact_retry(gcmd)
        epos = [start[0], start[1], z]
        self.results.append(manual_probe.create_probe_result(
            epos, self.get_offsets()))
    # Background monitor (emergency pause / stop, SenseOffset, flow comp.)
    def _handle_ready(self):
        if self.sensor.get_mcu().is_fileoutput():
            return
        self.monitor_timer = self.reactor.register_timer(
            self._monitor_event, self.reactor.NOW)
    def _handle_shutdown(self):
        if self.monitor_timer is not None:
            self.reactor.update_timer(self.monitor_timer, self.reactor.NEVER)
    def _print_state(self, eventtime):
        ps = self.printer.lookup_object('print_stats', None)
        if ps is None:
            return 'standby'
        return ps.get_status(eventtime)['state']
    def _motion(self, eventtime):
        """Return (z_moving, e_moving, current_z) from the trapq history."""
        mr = self.printer.lookup_object('motion_report', None)
        if mr is None:
            return False, False, None
        toolhead = self._toolhead()
        mcu = toolhead.get_kinematics().get_steppers()[0].get_mcu()
        pt = mcu.estimated_print_time(eventtime)
        dt = self.monitor_interval
        th = mr.dtrapqs.get('toolhead')
        pos_now, v = th.get_trapq_position(pt)
        pos_old, v = th.get_trapq_position(pt - dt)
        z_moving = False
        if pos_now is not None and pos_old is not None:
            dz = abs(pos_now[2] - pos_old[2])
            dxy = ((pos_now[0] - pos_old[0]) ** 2
                   + (pos_now[1] - pos_old[1]) ** 2) ** .5
            # Bed mesh corrections add small Z changes to XY moves (slopes
            # of a few percent).  Like the RF fw (isZMove) only count moves
            # that are mainly Z moves.
            z_moving = dz > 1e-6 and dz >= 0.2 * dxy
        e_moving = False
        ename = toolhead.get_extruder().get_name()
        etq = mr.dtrapqs.get(ename)
        if etq is not None:
            e_now, v = etq.get_trapq_position(pt)
            e_old, v = etq.get_trapq_position(pt - dt)
            e_moving = (e_now is not None and e_old is not None
                        and abs(e_now[0] - e_old[0]) > 1e-6)
        cur_z = pos_now[2] if pos_now is not None else None
        return z_moving, e_moving, cur_z
    def _run_gcode_later(self, script):
        def run(eventtime):
            try:
                self.gcode.run_script(script)
            except Exception:
                logging.exception("rf2000_dms: error running '%s'", script)
        self.reactor.register_callback(run)
    def _monitor_event(self, eventtime):
        next_time = eventtime + self.monitor_interval
        if not self.monitor_enabled or self.probing:
            self.pause_acc = []
            self.stop_acc = []
            return next_time
        try:
            val, fresh, t = self.read_value()
        except Exception:
            logging.exception("rf2000_dms: monitor read failed")
            return eventtime + 1.
        state = self._print_state(eventtime)
        printing = state == 'printing'
        z_moving, e_moving, cur_z = self._motion(eventtime)
        # Emergency pause while printing
        if printing and (self.pause_min or self.pause_max):
            self.pause_acc.append(val)
            if len(self.pause_acc) >= self.pause_checks:
                avg = sum(self.pause_acc) / float(len(self.pause_acc))
                self.pause_acc = []
                if not self.pause_min <= avg <= self.pause_max:
                    self.paused_by_dms = True
                    self.gcode.respond_info(
                        "DMS emergency pause: force %.0f digits outside"
                        " %d..%d" % (avg, self.pause_min, self.pause_max))
                    self._run_gcode_later(self.pause_gcode.render())
        else:
            self.pause_acc = []
        # Emergency stop: Z moving without extrusion and blocked
        if e_moving:
            self.last_extrude_time = eventtime
        recently_extruded = (eventtime - self.last_extrude_time
                             < self.stop_holdoff)
        if (z_moving and not e_moving and not recently_extruded
                and (self.stop_min or self.stop_max)):
            next_time = min(next_time, eventtime + self.stop_interval)
            if fresh:
                self.stop_acc.append(val)
            if len(self.stop_acc) >= self.stop_checks:
                avg = sum(self.stop_acc) / float(len(self.stop_acc))
                self.stop_acc = []
                if not self.stop_min <= avg <= self.stop_max:
                    self.printer.invoke_shutdown(
                        "DMS emergency stop: Z axis blocked (force %.0f"
                        " digits outside %d..%d)"
                        % (avg, self.stop_min, self.stop_max))
                    return self.reactor.NEVER
        else:
            self.stop_acc = []
        if printing:
            self._sense_offset_update(val, cur_z)
            self._flow_compensation_update(val, cur_z)
            self._pressure_control_update(val, cur_z, e_moving)
        elif state != 'paused' and (self.so_applied
                                    or self.fc_base is not None
                                    or self.pc_base is not None):
            # print finished/cancelled (a pause keeps the adjustments, RESUME
            # restores the g-code state they are part of)
            self._reset_live_adjustments()
        return next_time
    def _sense_offset_update(self, val, cur_z):
        if not self.so_max_digits or cur_z is None:
            return
        if cur_z > self.so_max_height + self.so_applied:
            return
        digits = abs(val)
        new = self.so_applied
        if digits > self.so_max_digits:
            new = min(self.so_max_offset, self.so_applied + self.so_step)
        elif digits < 0.8 * self.so_max_digits and self.so_applied > 0.:
            new = max(0., self.so_applied - self.so_step)
        if new != self.so_applied:
            delta = new - self.so_applied
            self.so_applied = new
            # MOVE=1: apply now, XY moves of the first layer carry no Z word
            self._run_gcode_later("SET_GCODE_OFFSET Z_ADJUST=%.4f MOVE=1"
                                  % (delta,))
    def _flow_compensation_update(self, val, cur_z):
        if not (self.fc_extrude or self.fc_speed) or self.fc_max <= self.fc_min:
            return
        digits = abs(val)
        # Same running average as the RF firmware (75% history)
        if self.fc_avg is None:
            self.fc_avg = digits
        self.fc_avg = 0.75 * self.fc_avg + 0.25 * digits
        if cur_z is not None and cur_z < self.fc_min_height:
            ratio = 0.
        elif self.fc_avg <= self.fc_min:
            ratio = 0.
        elif self.fc_avg >= self.fc_max:
            ratio = 1.
        else:
            ratio = (self.fc_avg - self.fc_min) / float(self.fc_max
                                                       - self.fc_min)
        if self.fc_base is None:
            gm = self.printer.lookup_object('gcode_move').get_status()
            self.fc_base = (gm['extrude_factor'], gm['speed_factor'])
        e_fac = 1. + 0.01 * self.fc_extrude * ratio
        s_fac = 1. + 0.01 * self.fc_speed * ratio
        script = []
        if abs(e_fac - self.fc_last_e) > 0.005:
            self.fc_last_e = e_fac
            script.append("M221 S%.1f" % (100. * self.fc_base[0] * e_fac,))
        if abs(s_fac - self.fc_last_s) > 0.005:
            self.fc_last_s = s_fac
            script.append("M220 S%.1f" % (100. * self.fc_base[1]
                                          * s_fac,))
        if script:
            self._run_gcode_later("\n".join(script))
    def _pressure_control_update(self, val, cur_z, e_moving):
        if not self.pc_target:
            return
        if not e_moving:
            # Travel / retraction - keep the last factor
            return
        digits = abs(val)
        if self.pc_avg is None:
            self.pc_avg = digits
        self.pc_avg = 0.75 * self.pc_avg + 0.25 * digits
        error = (self.pc_avg - self.pc_target) / float(self.pc_target)
        dt = self.monitor_interval
        # Only ever slow down / reduce flow below the user setting
        min_fac = min(self.pc_min_speed, self.pc_min_flow) / 100.
        if self.pc_mode == 'speed':
            min_fac = self.pc_min_speed / 100.
        elif self.pc_mode == 'flow':
            min_fac = self.pc_min_flow / 100.
        new_int = self.pc_integral + error * dt
        factor = 1. - (self.pc_kp * error + self.pc_ki * new_int)
        if min_fac <= factor <= 1.:
            self.pc_integral = new_int  # anti-windup: integrate unclamped
        elif factor > 1. and new_int < self.pc_integral:
            self.pc_integral = max(0., new_int)
        factor = max(min_fac, min(1., factor))
        if abs(factor - self.pc_factor) < 0.01:
            return
        self.pc_factor = factor
        if self.pc_base is None:
            gm = self.printer.lookup_object('gcode_move').get_status()
            self.pc_base = (gm['extrude_factor'], gm['speed_factor'])
        script = []
        if self.pc_mode in ('speed', 'both'):
            sfac = max(self.pc_min_speed / 100., factor)
            script.append("M220 S%.1f" % (100. * self.pc_base[1] * sfac,))
        if self.pc_mode in ('flow', 'both'):
            efac = max(self.pc_min_flow / 100., factor)
            script.append("M221 S%.1f" % (100. * self.pc_base[0] * efac,))
        self._run_gcode_later("\n".join(script))
    def _reset_live_adjustments(self):
        script = []
        if self.pc_base is not None:
            script.append("M221 S%.1f" % (100. * self.pc_base[0],))
            script.append("M220 S%.1f" % (100. * self.pc_base[1],))
            self.pc_base = None
            self.pc_avg = None
            self.pc_integral = 0.
            self.pc_factor = 1.
        if self.so_applied:
            homed = 'z' in self._toolhead().get_status(
                self.reactor.monotonic())['homed_axes']
            script.append("SET_GCODE_OFFSET Z_ADJUST=%.4f MOVE=%d"
                          % (-self.so_applied, homed))
            self.so_applied = 0.
        if self.fc_base is not None:
            script.append("M221 S%.1f" % (100. * self.fc_base[0],))
            script.append("M220 S%.1f" % (100. * self.fc_base[1],))
            self.fc_base = None
            self.fc_avg = None
            self.fc_last_e = self.fc_last_s = 1.
        if script:
            self._run_gcode_later("\n".join(script))
    # G-Code commands
    cmd_DMS_READ_help = "Report the current strain gauge value (digits)"
    def cmd_DMS_READ(self, gcmd):
        val, t = self.read_fresh()
        gcmd.respond_info("DMS: %d digits (raw %d, tare %d, idle %s)" % (
            val, val + self.tare, self.tare,
            "n/a" if self.last_idle is None else "%.0f" % self.last_idle))
    cmd_DMS_TARE_help = "Zero the strain gauge (nozzle must be free)"
    def cmd_DMS_TARE(self, gcmd):
        if gcmd.get_int('RESET', 0):
            self.tare = 0
        else:
            count = gcmd.get_int('SAMPLES', 8, minval=1)
            self._toolhead().wait_moves()
            vals = [self.sensor.read_fresh()[0] for i in range(count)]
            self.tare = int(round(sum(vals) / float(count)))
        gcmd.respond_info("DMS: tare offset %d digits" % (self.tare,))
    cmd_DMS_TEST_help = "Sample the strain gauge and report noise statistics"
    def cmd_DMS_TEST(self, gcmd):
        count = gcmd.get_int('SAMPLES', 30, minval=2)
        vals = [self.read_fresh()[0] for i in range(count)]
        avg = sum(vals) / float(count)
        sigma = (sum([(v - avg) ** 2 for v in vals]) / count) ** .5
        gcmd.respond_info(
            "DMS test (%d samples @ %.0f SPS): min %d max %d avg %.1f"
            " range %d stddev %.2f" % (count, self.sensor.sps, min(vals),
                                       max(vals), avg, max(vals) - min(vals),
                                       sigma))
        if max(vals) - min(vals) > self.idle_tolerance:
            gcmd.respond_info("WARNING: noise exceeds idle_tolerance (%d)"
                              % (self.idle_tolerance,))
    cmd_DMS_MONITOR_help = "Enable/disable the DMS emergency monitor"
    def cmd_DMS_MONITOR(self, gcmd):
        self.monitor_enabled = bool(gcmd.get_int('ENABLE',
                                                 int(self.monitor_enabled)))
        if gcmd.get_int('CLEAR', 0):
            self.paused_by_dms = False
        gcmd.respond_info("DMS monitor %s" % (
            "enabled" if self.monitor_enabled else "disabled"))
    cmd_DMS_SET_LIMITS_help = "Set DMS emergency pause/stop digit limits"
    def cmd_DMS_SET_LIMITS(self, gcmd):
        pmax = gcmd.get_int('PAUSE_MAX', self.pause_max, minval=1,
                            maxval=32767)
        pmin = gcmd.get_int('PAUSE_MIN', self.pause_min, minval=-32768,
                            maxval=-1)
        smax = gcmd.get_int('STOP_MAX', self.stop_max, minval=1,
                            maxval=32767)
        smin = gcmd.get_int('STOP_MIN', self.stop_min, minval=-32768,
                            maxval=-1)
        self.pause_min, self.pause_max = pmin, pmax
        self.stop_min, self.stop_max = smin, smax
        gcmd.respond_info("DMS limits: pause %d..%d stop %d..%d" % (
            self.pause_min, self.pause_max, self.stop_min, self.stop_max))
    def _probe_at(self, gcmd, x, y, contact_delta=None):
        toolhead = self._toolhead()
        curtime = self.reactor.monotonic()
        if 'xyz' not in toolhead.get_status(curtime)['homed_axes']:
            raise gcmd.error("Must home all axes first")
        params = self.get_probe_params(gcmd)
        start_z = gcmd.get_float('Z', 1.0)
        pos = toolhead.get_position()
        pos[2] = max(pos[2], start_z)
        toolhead.manual_move(pos, params['lift_speed'])
        toolhead.manual_move([x - self.x_offset, y - self.y_offset, None],
                             gcmd.get_float('XY_SPEED', 50., above=0.))
        toolhead.manual_move([None, None, start_z], params['lift_speed'])
        self.probing += 1
        try:
            z, idle, val = self._probe_contact_retry(gcmd, contact_delta)
        finally:
            self.probing = max(0, self.probing - 1)
        return z - self.z_offset
    cmd_DMS_Z_OFFSET_SCAN_help = ("Probe one point and set the g-code Z"
                                  " offset so Z0 touches the bed (RF M3900)")
    def cmd_DMS_Z_OFFSET_SCAN(self, gcmd):
        toolhead = self._toolhead()
        kin_status = toolhead.get_kinematics().get_status(
            self.reactor.monotonic())
        amin, amax = kin_status['axis_minimum'], kin_status['axis_maximum']
        x = gcmd.get_float('X', (amin[0] + amax[0]) / 2.)
        y = gcmd.get_float('Y', (amin[1] + amax[1]) / 2.)
        learn = gcmd.get_float('S', 100., minval=0., maxval=100.) / 100.
        try:
            measured = self._probe_at(gcmd, x, y)
        except self.printer.command_error as e:
            if 'soft contact' not in str(e):
                raise
            # The saved mesh is in the same frame as homing (repeatable to a
            # few um), so printing on without a blob-shifted offset is safer
            gcmd.respond_info("WARNING: %s - z-offset scan skipped, g-code Z"
                              " offset unchanged" % (e,))
            return
        mesh_z = 0.
        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        if bed_mesh is not None and bed_mesh.get_mesh() is not None:
            mesh_z = bed_mesh.get_mesh().calc_z(x, y)
        gm = self.printer.lookup_object('gcode_move')
        current = gm.get_status()['homing_origin'][2]
        target = measured - mesh_z + self.scan_trim
        new = current + (target - current) * learn
        self.gcode.run_script_from_command(
            "SET_GCODE_OFFSET Z=%.4f MOVE=0" % (new,))
        gcmd.respond_info(
            "DMS z-offset scan at X%.1f Y%.1f: contact Z=%.4f mesh=%.4f"
            " trim=%.3f -> g-code Z offset %.4f (was %.4f)"
            % (x, y, measured, mesh_z, self.scan_trim, new, current))
    cmd_DMS_FIND_Z_ORIGIN_help = ("Milling: move the bed up until the tool"
                                  " touches the work part and set Z=0")
    def cmd_DMS_FIND_Z_ORIGIN(self, gcmd):
        toolhead = self._toolhead()
        pos = toolhead.get_position()
        delta = gcmd.get_int('CONTACT_DELTA', 500, minval=1)
        self.probing += 1
        try:
            z, idle, val = self.probe_contact(gcmd, delta,
                                              check_stiffness=False)
        finally:
            self.probing = max(0, self.probing - 1)
        cur = toolhead.get_position()[2]
        # Make the contact height g-code Z=0
        self.gcode.run_script_from_command("G92 Z%.4f" % (cur - z,))
        gcmd.respond_info("DMS: Z origin set at contact (Z=%.4f)" % (z,))
    cmd_DMS_SENSE_OFFSET_help = ("SenseOffset: lower the bed when the force"
                                 " exceeds DIGITS on the first layer (RF M3909)")
    def cmd_DMS_SENSE_OFFSET(self, gcmd):
        self.so_max_digits = gcmd.get_int('DIGITS', self.so_max_digits,
                                          minval=0, maxval=14999)
        self.so_max_offset = gcmd.get_float('MAX_OFFSET', self.so_max_offset,
                                            minval=0., maxval=0.300)
        if not self.so_max_digits and self.so_applied:
            self._reset_live_adjustments()
        gcmd.respond_info("DMS SenseOffset: %s (max %.3fmm, applied %.4fmm)"
                          % ("%d digits" % self.so_max_digits
                             if self.so_max_digits else "off",
                             self.so_max_offset, self.so_applied))
    def cmd_M3909(self, gcmd):
        # M3909 P<max digits> S<max offset um>
        params = {'DIGITS': gcmd.get('P', str(self.so_max_digits))}
        if gcmd.get('S', None) is not None:
            params['MAX_OFFSET'] = str(gcmd.get_float('S') / 1000.)
        self.cmd_DMS_SENSE_OFFSET(self.gcode.create_gcode_command(
            "DMS_SENSE_OFFSET", "DMS_SENSE_OFFSET", params))
    cmd_DMS_FLOW_COMPENSATION_help = ("Reduce flow/speed with rising force"
                                      " (RF M3911)")
    def cmd_DMS_FLOW_COMPENSATION(self, gcmd):
        self.fc_min = gcmd.get_int('MIN_DIGITS', self.fc_min, minval=0)
        self.fc_max = gcmd.get_int('MAX_DIGITS', self.fc_max, minval=0)
        self.fc_extrude = gcmd.get_float('EXTRUDE', self.fc_extrude,
                                         minval=-99., maxval=99.)
        self.fc_speed = gcmd.get_float('SPEED', self.fc_speed, minval=-99.,
                                       maxval=99.)
        if gcmd.get_int('OFF', 0):
            self.fc_extrude = self.fc_speed = 0.
        if (self.fc_extrude or self.fc_speed) and self.pc_target:
            self.pc_target = 0
            gcmd.respond_info("DMS pressure control switched off (only one"
                              " M220/M221 controller can be active)")
        if not (self.fc_extrude or self.fc_speed):
            self._reset_live_adjustments()
        gcmd.respond_info(
            "DMS flow compensation: %d..%d digits -> extrude %+.0f%%"
            " speed %+.0f%%" % (self.fc_min, self.fc_max, self.fc_extrude,
                                self.fc_speed))
    def cmd_M3911(self, gcmd):
        # M3911 S<min digits> P<max digits> E<extrude %> F<speed %>
        params = {}
        for src, dst in (('S', 'MIN_DIGITS'), ('P', 'MAX_DIGITS'),
                         ('E', 'EXTRUDE'), ('F', 'SPEED')):
            if gcmd.get(src, None) is not None:
                params[dst] = gcmd.get(src)
        self.cmd_DMS_FLOW_COMPENSATION(self.gcode.create_gcode_command(
            "DMS_FLOW_COMPENSATION", "DMS_FLOW_COMPENSATION", params))

    cmd_DMS_PRESSURE_CONTROL_help = ("Closed loop: hold nozzle force at"
                                     " TARGET digits by lowering speed/flow")
    def cmd_DMS_PRESSURE_CONTROL(self, gcmd):
        self.pc_target = gcmd.get_int('TARGET', self.pc_target, minval=0)
        if gcmd.get_int('OFF', 0):
            self.pc_target = 0
        self.pc_kp = gcmd.get_float('KP', self.pc_kp, minval=0.)
        self.pc_ki = gcmd.get_float('KI', self.pc_ki, minval=0.)
        self.pc_min_speed = gcmd.get_float('MIN_SPEED', self.pc_min_speed,
                                           minval=5., maxval=100.)
        self.pc_min_flow = gcmd.get_float('MIN_FLOW', self.pc_min_flow,
                                          minval=50., maxval=100.)
        mode = gcmd.get('MODE', self.pc_mode).lower()
        if mode not in ('speed', 'flow', 'both'):
            raise gcmd.error("MODE must be speed, flow or both")
        self.pc_mode = mode
        if self.pc_target and (self.fc_extrude or self.fc_speed):
            self.fc_extrude = self.fc_speed = 0.
            gcmd.respond_info("DMS flow compensation (M3911) switched off"
                              " (only one M220/M221 controller can be"
                              " active)")
        if not self.pc_target:
            self._reset_live_adjustments()
        gcmd.respond_info(
            "DMS pressure control: %s mode=%s kp=%.2f ki=%.2f min_speed=%.0f%%"
            " min_flow=%.0f%%" % (
                "target %d digits" % self.pc_target if self.pc_target
                else "off", self.pc_mode, self.pc_kp, self.pc_ki,
                self.pc_min_speed, self.pc_min_flow))
    def _extrude_and_sample(self, length, speed):
        """Queue an extrude-only move and return force samples taken
        during the second half of the move (steady state)."""
        toolhead = self._toolhead()
        reactor = self.reactor
        pos = toolhead.get_position()
        toolhead.wait_moves()
        mcu = self.sensor.get_mcu()
        start = toolhead.get_last_move_time()
        pos[3] += length
        toolhead.manual_move(pos, speed)
        if mcu.is_fileoutput():
            # batch/simulation mode: no clock to wait for, no sensor
            return []
        end = toolhead.get_last_move_time()
        mid = start + 0.5 * (end - start)
        samples = []
        while 1:
            now = reactor.monotonic()
            pt = mcu.estimated_print_time(now)
            if pt >= end:
                break
            if pt < mid:
                reactor.pause(now + min(0.05, mid - pt))
                continue
            val, t = self.read_fresh()
            samples.append(val)
        toolhead.wait_moves()
        return samples
    cmd_DMS_VISCOSITY_TEST_help = ("Measure nozzle force vs. extrusion speed"
                                   " (CSV) and recommend a max flow (RF M3939)")
    def cmd_DMS_VISCOSITY_TEST(self, gcmd):
        toolhead = self._toolhead()
        extruder = toolhead.get_extruder()
        area = getattr(extruder, 'filament_area', 2.405)
        v = gcmd.get_float('START', 0.5, above=0.)
        v_end = gcmd.get_float('END', 5.0, above=v)
        inc = gcmd.get_float('STEP', 0.25, above=0.)
        length = gcmd.get_float('LENGTH', 5., above=0.)
        max_digits = gcmd.get_int('MAX_DIGITS', 10000, minval=1)
        limit_digits = gcmd.get_int('LIMIT_DIGITS', 0, minval=0)
        self.probing += 1  # suspend the emergency monitor
        try:
            idle = self.measure_idle()
            lines = ["speed_mm_s,flow_mm3_s,avg_digits,max_digits"]
            best = None
            while v <= v_end + 1e-9:
                samples = self._extrude_and_sample(length, v)
                if not samples:
                    v += inc
                    continue
                rel = [abs(s - idle) for s in samples]
                avg = sum(rel) / float(len(rel))
                flow = v * area
                lines.append("%.2f,%.2f,%.0f,%.0f" % (v, flow, avg, max(rel)))
                gcmd.respond_info(lines[-1])
                if limit_digits and avg <= limit_digits:
                    best = flow
                if max(rel) >= max_digits:
                    gcmd.respond_info("MAX_DIGITS reached - stopping")
                    break
                v += inc
        finally:
            self.probing = max(0, self.probing - 1)
        gcmd.respond_info("DMS viscosity test (idle %.0f):\n%s"
                          % (idle, "\n".join(lines)))
        if best is not None:
            gcmd.respond_info(
                "Highest flow below %d digits: %.1f mm^3/s - use this as the"
                " slicer max volumetric speed, or set"
                " DMS_PRESSURE_CONTROL TARGET=%d" % (limit_digits, best,
                                                     limit_digits))
    def cmd_M3939(self, gcmd):
        # M3939 F<max digits> S<start temp> P<end temp> E<max mm/s>
        #       I<increment mm/s>
        params = {'MAX_DIGITS': gcmd.get('F', '10000'),
                  'END': gcmd.get('E', '5'), 'STEP': gcmd.get('I', '0.1')}
        temps = [gcmd.get_float('S', None), gcmd.get_float('P', None)]
        heater = self._toolhead().get_extruder().get_name()
        if temps[0] is not None:
            self.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=%.0f\n"
                "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.0f MAXIMUM=%.0f" % (
                    heater, temps[0], heater, temps[0] - 2., temps[0] + 2.))
        self.cmd_DMS_VISCOSITY_TEST(self.gcode.create_gcode_command(
            "DMS_VISCOSITY_TEST", "DMS_VISCOSITY_TEST", params))
        if temps[1] is not None and temps[1] != temps[0]:
            self.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=%.0f\n"
                "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.0f MAXIMUM=%.0f" % (
                    heater, temps[1], heater, temps[1] - 2., temps[1] + 2.))
            self.cmd_DMS_VISCOSITY_TEST(self.gcode.create_gcode_command(
                "DMS_VISCOSITY_TEST", "DMS_VISCOSITY_TEST", params))
    def _extrude_until_force(self, gcmd, digits, speed, max_length, chunk):
        idle = self.measure_idle()
        done = 0.
        while done < max_length:
            samples = self._extrude_and_sample(chunk, speed)
            done += chunk
            if samples and max([abs(s - idle) for s in samples]) >= digits:
                return done, True
        return done, False
    cmd_DMS_LOAD_FILAMENT_help = ("Extrude until the nozzle force shows the"
                                  " hotend is filled (RF M3913)")
    def cmd_DMS_LOAD_FILAMENT(self, gcmd):
        digits = gcmd.get_int('DIGITS', 3500, minval=1)
        speed = gcmd.get_float('SPEED', 3., above=0.)
        max_len = gcmd.get_float('MAX_LENGTH', 120., above=0.)
        self.probing += 1
        try:
            length, ok = self._extrude_until_force(gcmd, digits, speed,
                                                   max_len, 2.)
        finally:
            self.probing = max(0, self.probing - 1)
        gcmd.respond_info("DMS load: %s after %.0fmm" % (
            "nozzle filled" if ok else "force NOT reached", length))
    def cmd_M3913(self, gcmd):
        params = {'DIGITS': gcmd.get('P', '3500'),
                  'SPEED': gcmd.get('F', '3')}
        self.cmd_DMS_LOAD_FILAMENT(self.gcode.create_gcode_command(
            "DMS_LOAD_FILAMENT", "DMS_LOAD_FILAMENT", params))
    cmd_DMS_UNLOAD_FILAMENT_help = ("Purge until force DIGITS (softens the"
                                    " tip) then retract LENGTH (RF M3914)")
    def cmd_DMS_UNLOAD_FILAMENT(self, gcmd):
        digits = gcmd.get_int('DIGITS', 4500, minval=1)
        length = gcmd.get_float('LENGTH', 90., above=0.)
        self.probing += 1
        try:
            self._extrude_until_force(gcmd, digits, 2., 20., 1.)
            toolhead = self._toolhead()
            speed = gcmd.get_float('SPEED', 8.3, above=0.)
            # E-only moves are limited by max_extrude_only_distance (50mm
            # default) - retract in chunks
            left = length
            while left > 0.:
                step = min(left, 40.)
                pos = toolhead.get_position()
                pos[3] -= step
                toolhead.manual_move(pos, speed)
                left -= step
            toolhead.wait_moves()
        finally:
            self.probing = max(0, self.probing - 1)
        gcmd.respond_info("DMS unload: retracted %.0fmm" % (length,))
    def cmd_M3914(self, gcmd):
        params = {'DIGITS': gcmd.get('P', '4500')}
        self.cmd_DMS_UNLOAD_FILAMENT(self.gcode.create_gcode_command(
            "DMS_UNLOAD_FILAMENT", "DMS_UNLOAD_FILAMENT", params))


def load_config(config):
    return RF2000DMS(config)
