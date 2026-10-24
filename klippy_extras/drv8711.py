# Support for the TI DRV8711 stepper pre-driver (Renkforce RF1000/RF2000/
# RF2000v2 mainboards)
#
# The DRV8711 powers up with its H-bridges disabled (CTRL.ENBL=0) and has
# no default micro-stepping/current that matches the printer, so it must be
# programmed over SPI before any motor can move.  On the RF2000v2 the five
# drivers share one bit-banged SPI bus (SCLK/SDATI/SDATO) and each driver
# has its own chip select which is *active high*.
#
# Copyright (C) 2026  klipper-rf2000v2 contributors
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import bus

# Register addresses
REG_CTRL, REG_TORQUE, REG_OFF, REG_BLANK = 0x00, 0x01, 0x02, 0x03
REG_DECAY, REG_STALL, REG_DRIVE, REG_STATUS = 0x04, 0x05, 0x06, 0x07
REG_NAMES = ['CTRL', 'TORQUE', 'OFF', 'BLANK', 'DECAY', 'STALL', 'DRIVE',
             'STATUS']

# Factory register values used by the Conrad RF2000v2 firmware
# (RF.01.xx, Configuration.h DRV8711_REGISTER_0x).  Only the 12 data bits
# are stored here; the address is added when the frame is built.
DEFAULT_OFF = 0x097     # TOFF=0x97, PWMMODE=0 (internal indexer)
DEFAULT_BLANK = 0x1D7   # ABT=1, TBLANK=0xD7
DEFAULT_DECAY = 0x430   # DECMOD=100 (auto mixed decay), TDECAY=0x30
DEFAULT_STALL = 0x83C   # VDIV=10, SDCNT=00, SDTHR=0x3C
DEFAULT_DRIVE = 0x0F0   # TDRIVEN=11, TDRIVEP=11, IDRIVE*=00, OCP*=00
DEFAULT_SMPLTH = 1      # TORQUE.SMPLTH=001 (100us), as used by the RF fw

ISGAIN_CHOICES = {5: 0, 10: 1, 20: 2, 40: 3}
DTIME_CHOICES = {400: 0, 450: 1, 650: 2, 850: 3}
STATUS_BITS = ['OTS', 'AOCP', 'BOCP', 'APDF', 'BPDF', 'UVLO', 'STD',
               'STDLAT']
# Bits that stop the H-bridge (over-temperature, over-current, pre-driver).
# UVLO, STD and STDLAT are informational: on a real RF2000v2 both extruder
# drivers report UVLO (and pull FAULTn low while enabled) although the
# motors run normally, and STD/STDLAT are always set on a motor at rest.
FAULT_MASK = 0x1f
MICROSTEP_MODES = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5, 64: 6, 128: 7,
                   256: 8}
VREF = 2.75
RESET_PULSE_TIME = 0.100


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


def decode_status(status, mask=0xff):
    if status is None:
        return []
    return [STATUS_BITS[i] for i in range(8) if status & mask & (1 << i)]


def build_frame(addr, data, read=False):
    word = ((0x8 if read else 0x0) | (addr & 0x7)) << 12 | (data & 0xfff)
    return [(word >> 8) & 0xff, word & 0xff]


def calc_ctrl(microsteps, isgain, dtime, enable=True, exstall=False):
    return ((DTIME_CHOICES[dtime] << 10) | (ISGAIN_CHOICES[isgain] << 8)
            | (int(exstall) << 7) | (MICROSTEP_MODES[microsteps] << 3)
            | int(enable))


def torque_to_current(torque, isgain, sense_resistor):
    return VREF * torque / (256. * isgain * sense_resistor)


def current_to_torque(current, isgain, sense_resistor):
    return int(round(current * 256. * isgain * sense_resistor / VREF))


class DRV8711ResetHelper:
    """Shared object that owns the common RESET line of all drivers and
    programs every registered driver once the MCU is connected."""
    def __init__(self, printer):
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.drivers = []
        self.reset_pin = None
        # The "off" command is only sent once the MCU reaches the "on" time
        # (minclock), so a pulse shorter than the serial round trip can
        # arrive after its own deadline -> "Timer too close" shutdown (1 of
        # 8 restarts with 5 ms on a real RF2000v2).  Klipper keeps host
        # scheduled pin changes >= 100 ms apart for the same reason.
        self.reset_time = RESET_PULSE_TIME
        printer.register_event_handler("klippy:connect", self._handle_connect)
    def setup_reset_pin(self, config, pin_desc):
        if self.reset_pin is not None:
            raise config.error("drv8711: reset_pin may only be specified"
                               " in one [drv8711] section")
        ppins = self.printer.lookup_object('pins')
        self.reset_pin = ppins.setup_pin('digital_out', pin_desc)
        self.reset_pin.setup_max_duration(0.)
        # Reset is active high - keep it released
        self.reset_pin.setup_start_value(0, 0)
        self.reset_time = config.getfloat('reset_pulse_time',
                                          RESET_PULSE_TIME, minval=0.100,
                                          maxval=0.500)
    def register_driver(self, driver):
        self.drivers.append(driver)
    def _pulse_reset(self):
        mcu = self.reset_pin.get_mcu()
        curtime = self.reactor.monotonic()
        print_time = mcu.estimated_print_time(curtime) + 0.100
        self.reset_pin.set_digital(print_time, 1)
        self.reset_pin.set_digital(print_time + self.reset_time, 0)
        # Wait for the pulse to complete plus the same settling time the
        # RF firmware uses after reset
        self.reactor.pause(curtime + 0.100 + 2. * self.reset_time + 0.010)
    def _handle_connect(self):
        if self.reset_pin is not None:
            self._pulse_reset()
        for driver in self.drivers:
            driver.init_registers()


class DRV8711:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.stepper_name = self.name
        # SPI (software bus, chip select active high)
        self.spi = bus.MCU_SPI_from_config(config, 0, default_speed=100000,
                                           cs_active_high=True)
        # Reset helper shared by all drivers
        self.helper = printer.lookup_object('drv8711_reset', None)
        if self.helper is None:
            self.helper = DRV8711ResetHelper(printer)
            printer.add_object('drv8711_reset', self.helper)
        reset_pin = config.get('reset_pin', None)
        if reset_pin is not None:
            self.helper.setup_reset_pin(config, reset_pin)
        self.helper.register_driver(self)
        # Micro-stepping comes from the [stepper_x] section (like TMC)
        sconfig = config.getsection(self.stepper_name)
        self.microsteps = sconfig.getint('microsteps')
        if self.microsteps not in MICROSTEP_MODES:
            raise config.error("drv8711 %s: microsteps must be a power of"
                               " two between 1 and 256" % (self.name,))
        # Current sense amplifier and current settings
        self.isgain = config.getchoice('sense_amp_gain',
                                       {str(k): k for k in ISGAIN_CHOICES},
                                       '20')
        self.dtime = config.getchoice('dead_time',
                                      {str(k): k for k in DTIME_CHOICES},
                                      '850')
        self.sense_resistor = config.getfloat('sense_resistor', None,
                                              above=0.)
        run_current = config.getfloat('run_current', None, above=0.)
        if run_current is not None:
            if self.sense_resistor is None:
                raise config.error("drv8711 %s: run_current requires"
                                   " sense_resistor" % (self.name,))
            self.torque = current_to_torque(run_current, self.isgain,
                                            self.sense_resistor)
            if not 1 <= self.torque <= 255:
                raise config.error("drv8711 %s: run_current out of range"
                                   % (self.name,))
        else:
            self.torque = config.getint('torque', minval=1, maxval=255)
        self.run_torque = self.torque
        self.smplth = config.getint('stall_sample_threshold', DEFAULT_SMPLTH,
                                    minval=0, maxval=7)
        # Raw register overrides (12 data bits, factory RF2000v2 defaults)
        self.reg_off = config_getint_hex(config, 'reg_off', DEFAULT_OFF,
                                         0, 0x1ff)
        self.reg_blank = config_getint_hex(config, 'reg_blank', DEFAULT_BLANK,
                                           0, 0x1ff)
        self.reg_decay = config_getint_hex(config, 'reg_decay', DEFAULT_DECAY,
                                           0, 0x7ff)
        self.reg_stall = config_getint_hex(config, 'reg_stall', DEFAULT_STALL,
                                           0, 0xfff)
        self.reg_drive = config_getint_hex(config, 'reg_drive', DEFAULT_DRIVE,
                                           0, 0xfff)
        self.verify = config.getboolean('verify_registers', False)
        self.disable_on_shutdown = config.getboolean('disable_on_shutdown',
                                                     True)
        self.shadow = {}
        self.initialized = False
        self.last_status = None
        if self.disable_on_shutdown:
            # Turn the H-bridges off if the MCU enters shutdown
            ctrl_off = calc_ctrl(self.microsteps, self.isgain, self.dtime,
                                 enable=False)
            self.spi.setup_shutdown_msg(build_frame(REG_CTRL, ctrl_off))
        # G-Code commands
        gcode = printer.lookup_object('gcode')
        gcode.register_mux_command("DRV_STATUS", "STEPPER", self.name,
                                   self.cmd_DRV_STATUS,
                                   desc=self.cmd_DRV_STATUS_help)
        gcode.register_mux_command("DRV_DUMP", "STEPPER", self.name,
                                   self.cmd_DRV_DUMP,
                                   desc=self.cmd_DRV_DUMP_help)
        gcode.register_mux_command("SET_DRV_CURRENT", "STEPPER",
                                   self.name, self.cmd_SET_DRV_CURRENT,
                                   desc=self.cmd_SET_DRV_CURRENT_help)
        gcode.register_mux_command("DRV_INIT", "STEPPER", self.name,
                                   self.cmd_DRV_INIT,
                                   desc=self.cmd_DRV_INIT_help)
    # Low level register access
    def write_reg(self, addr, data, print_time=None):
        self.shadow[addr] = data & 0xfff
        minclock = 0
        if print_time is not None:
            minclock = self.spi.get_mcu().print_time_to_clock(print_time)
        self.spi.spi_send(build_frame(addr, data), minclock=minclock)
    def read_reg(self, addr):
        if self.spi.get_mcu().is_fileoutput():
            return self.shadow.get(addr, 0)
        params = self.spi.spi_transfer(build_frame(addr, 0, read=True))
        resp = bytearray(params['response'])
        return ((resp[0] << 8) | resp[1]) & 0xfff
    def get_ctrl(self, enable=True):
        return calc_ctrl(self.microsteps, self.isgain, self.dtime, enable)
    def get_torque_reg(self, torque):
        return (self.smplth << 8) | (torque & 0xff)
    def init_registers(self):
        # Same order as the RF firmware: CTRL, OFF..DRIVE, STATUS, TORQUE
        self.write_reg(REG_CTRL, self.get_ctrl(True))
        self.write_reg(REG_OFF, self.reg_off)
        self.write_reg(REG_BLANK, self.reg_blank)
        self.write_reg(REG_DECAY, self.reg_decay)
        self.write_reg(REG_STALL, self.reg_stall)
        self.write_reg(REG_DRIVE, self.reg_drive)
        self.write_reg(REG_STATUS, 0x000)  # clear latched faults
        self.write_reg(REG_TORQUE, self.get_torque_reg(self.torque))
        self.initialized = True
        if self.verify:
            self._verify_registers()
    def _verify_registers(self):
        bad = []
        for addr in range(REG_CTRL, REG_DRIVE + 1):
            val = self.read_reg(addr)
            if val != self.shadow[addr]:
                bad.append("%s=0x%03x (expected 0x%03x)" % (
                    REG_NAMES[addr], val, self.shadow[addr]))
        if bad:
            msg = ("drv8711 %s: register readback mismatch: %s. Check the"
                   " SDATO pull-up / spi_speed." % (self.name, ", ".join(bad)))
            logging.warning(msg)
            self.printer.lookup_object('gcode').respond_info(msg)
    def _set_torque(self, torque, print_time=None):
        self.torque = torque
        self.write_reg(REG_TORQUE, self.get_torque_reg(torque), print_time)
    def get_current(self):
        if self.sense_resistor is None:
            return None
        return torque_to_current(self.torque, self.isgain,
                                 self.sense_resistor)
    def get_status(self, eventtime=None):
        return {'torque': self.torque, 'run_torque': self.run_torque,
                'microsteps': self.microsteps,
                'current': self.get_current(),
                'initialized': self.initialized,
                'status': self.last_status,
                'faults': decode_status(self.last_status, FAULT_MASK)}
    # G-Code commands
    cmd_DRV_STATUS_help = "Read and decode the DRV8711 STATUS register"
    def cmd_DRV_STATUS(self, gcmd):
        self.last_status = status = self.read_reg(REG_STATUS)
        faults = decode_status(status, FAULT_MASK)
        info = decode_status(status, ~FAULT_MASK & 0xff)
        gcmd.respond_info("DRV8711 %s: STATUS=0x%03x %s%s" % (
            self.name, status,
            "FAULT " + " ".join(faults) if faults else "(no faults)",
            " info: " + " ".join(info) if info else ""))
        if gcmd.get_int('CLEAR', 0):
            self.write_reg(REG_STATUS, 0x000)
            gcmd.respond_info("DRV8711 %s: faults cleared" % (self.name,))
    cmd_DRV_DUMP_help = "Read back all DRV8711 registers"
    def cmd_DRV_DUMP(self, gcmd):
        lines = []
        for addr in range(8):
            val = self.read_reg(addr)
            exp = self.shadow.get(addr)
            lines.append("%-6s 0x%03x%s" % (
                REG_NAMES[addr], val,
                "" if exp is None or exp == val or addr == REG_STATUS
                else " (written 0x%03x)" % (exp,)))
        gcmd.respond_info("DRV8711 %s:\n%s" % (self.name, "\n".join(lines)))
    cmd_SET_DRV_CURRENT_help = ("Set DRV8711 TORQUE (or CURRENT in Amps);"
                                    " RESTORE=1 returns to run torque")
    def cmd_SET_DRV_CURRENT(self, gcmd):
        torque = gcmd.get_int('TORQUE', None, minval=1, maxval=255)
        current = gcmd.get_float('CURRENT', None, above=0.)
        if gcmd.get_int('RESTORE', 0):
            torque = self.run_torque
        if current is not None:
            if self.sense_resistor is None:
                raise gcmd.error("CURRENT requires sense_resistor in config")
            torque = current_to_torque(current, self.isgain,
                                       self.sense_resistor)
            if not 1 <= torque <= 255:
                raise gcmd.error("Requested current out of range")
        if torque is not None:
            toolhead = self.printer.lookup_object('toolhead')
            print_time = toolhead.get_last_move_time()
            if not gcmd.get_int('TEMPORARY', 0):
                self.run_torque = torque
            self._set_torque(torque, print_time)
        cur = self.get_current()
        gcmd.respond_info("DRV8711 %s: torque=%d%s" % (
            self.name, self.torque,
            "" if cur is None else " (%.3fA)" % (cur,)))
    cmd_DRV_INIT_help = "Re-send the full DRV8711 configuration"
    def cmd_DRV_INIT(self, gcmd):
        self.init_registers()
        gcmd.respond_info("DRV8711 %s: registers written" % (self.name,))


def load_config_prefix(config):
    return DRV8711(config)
