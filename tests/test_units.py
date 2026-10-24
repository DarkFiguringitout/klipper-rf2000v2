#!/usr/bin/env python3
"""Unit tests for the klipper-rf2000v2 extras.

Run: KLIPPER_DIR=~/klipper python3 tests/test_units.py
(the extras are imported as klippy "extras" modules, so they must be linked
into $KLIPPER_DIR/klippy/extras - scripts/install.sh and
tests/run_klippy_batch.sh do that).
"""
import os
import re
import sys
import glob
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KLIPPER = os.path.expanduser(os.environ.get('KLIPPER_DIR', '~/klipper'))
sys.path.insert(0, os.path.join(KLIPPER, 'klippy'))
for f in glob.glob(os.path.join(REPO, 'klippy_extras', '*.py')):
    dst = os.path.join(KLIPPER, 'klippy', 'extras', os.path.basename(f))
    if not os.path.exists(dst):
        os.symlink(f, dst)

from extras import drv8711, rf2000_eeprom, rf2000_dms  # noqa: E402


class TestDRV8711(unittest.TestCase):
    def word(self, frame):
        return (frame[0] << 8) | frame[1]

    def test_ctrl_matches_conrad_register_00(self):
        # Conrad RF2000v2 Configuration.h DRV8711_REGISTER_00 per micro step
        conrad = {4: 0x0E11, 8: 0x0E19, 16: 0x0E21, 32: 0x0E29, 64: 0x0E31}
        for ms, reg in conrad.items():
            ctrl = drv8711.calc_ctrl(ms, 20, 850, enable=True)
            frame = drv8711.build_frame(drv8711.REG_CTRL, ctrl)
            self.assertEqual(self.word(frame), reg, "microsteps %d" % ms)

    def test_default_registers_match_conrad(self):
        expect = {drv8711.REG_OFF: (drv8711.DEFAULT_OFF, 0x2097),
                  drv8711.REG_BLANK: (drv8711.DEFAULT_BLANK, 0x31D7),
                  drv8711.REG_DECAY: (drv8711.DEFAULT_DECAY, 0x4430),
                  drv8711.REG_STALL: (drv8711.DEFAULT_STALL, 0x583C),
                  drv8711.REG_DRIVE: (drv8711.DEFAULT_DRIVE, 0x60F0),
                  drv8711.REG_STATUS: (0x000, 0x7000)}
        for addr, (data, reg) in expect.items():
            self.assertEqual(self.word(drv8711.build_frame(addr, data)), reg)

    def test_torque_frame_matches_rf_firmware(self):
        # setMotorCurrent(): command = 0x1100 + level
        for level in (32, 90, 100, 145, 255):
            data = (drv8711.DEFAULT_SMPLTH << 8) | level
            frame = drv8711.build_frame(drv8711.REG_TORQUE, data)
            self.assertEqual(self.word(frame), 0x1100 + level)

    def test_read_frame(self):
        # read STATUS: bit 15 set, address 7
        self.assertEqual(self.word(drv8711.build_frame(7, 0, read=True)),
                         0xF000)

    def test_status_fault_mask(self):
        # 0x0e0 = UVLO STD STDLAT as read on a real RF2000v2 extruder driver
        self.assertEqual(drv8711.decode_status(0x0e0, drv8711.FAULT_MASK), [])
        self.assertEqual(drv8711.decode_status(0x0e3, drv8711.FAULT_MASK),
                         ["OTS", "AOCP"])
        self.assertEqual(drv8711.decode_status(None), [])

    def test_current_roundtrip(self):
        t = drv8711.current_to_torque(1.5, 20, 0.05)
        self.assertAlmostEqual(drv8711.torque_to_current(t, 20, 0.05), 1.5,
                               delta=0.01)


def make_sector(xs, ys, zsteps, microsteps=32, fmt=7):
    """Build an RF EEPROM sector (big endian words) like the RF fw."""
    dim_x, dim_y = len(xs), len(ys)
    words = [0] * (rf2000_eeprom.SECTOR_MATRIX // 2)
    words[0] = fmt
    words[1] = dim_x
    words[2] = dim_y
    words[3] = microsteps
    m = [[0] * (dim_y + 1) for i in range(dim_x + 1)]
    m[0][0] = fmt
    for i, x in enumerate(xs):
        m[i + 1][0] = x
    for j, y in enumerate(ys):
        m[0][j + 1] = y
    for i in range(dim_x):
        for j in range(dim_y):
            m[i + 1][j + 1] = zsteps[j][i]
    for i in range(dim_x + 1):
        for j in range(dim_y + 1):
            words.append(m[i][j] & 0xffff)
    data = bytearray()
    for w in words:
        data += bytearray([(w >> 8) & 0xff, w & 0xff])
    return data + bytearray(rf2000_eeprom.SECTOR_SIZE - len(data))


class TestEEPROM(unittest.TestCase):
    def test_parse_and_resample(self):
        xs = [0, 20, 40, 60, 80, 100]
        ys = [30, 50, 70, 90]
        # z in micro steps: tilted plane z = -(x + 2y) * 0.001mm
        spm = 80 * 32
        zsteps = [[int(round(-(x + 2 * y) * 0.001 * spm)) for x in xs]
                  for y in ys]
        sector = make_sector(xs, ys, zsteps)
        pxs, pys, zmm, ms = rf2000_eeprom.parse_matrix(sector, 80.)
        self.assertEqual(pxs, xs)
        self.assertEqual(pys, ys)
        self.assertEqual(ms, 32)
        self.assertAlmostEqual(zmm[0][0], -(0 + 60) * 0.001, places=3)
        # a plane must survive bilinear resampling exactly
        mnx, mxx, mny, mxy, m = rf2000_eeprom.resample(pxs, pys, zmm, 11, 7)
        for yi, row in enumerate(m):
            y = mny + (mxy - mny) * yi / 6.
            for xi, z in enumerate(row):
                x = mnx + (mxx - mnx) * xi / 10.
                self.assertAlmostEqual(z, -(x + 2 * y) * 0.001, places=3)

    def test_negative_values_and_irregular_grid(self):
        xs = [0, 15, 35, 55, 75, 95, 115, 135, 155, 170]
        ys = [30, 50, 70]
        zsteps = [[-500 - i for i in range(len(xs))] for j in ys]
        sector = make_sector(xs, ys, zsteps, microsteps=16)
        pxs, pys, zmm, ms = rf2000_eeprom.parse_matrix(sector, 80.)
        self.assertEqual(ms, 16)
        self.assertAlmostEqual(zmm[0][0], -500 / (80. * 16))

    def test_rejects_bad_format(self):
        sector = make_sector([0, 10, 20], [0, 10, 20],
                             [[0] * 3] * 3, fmt=6)
        with self.assertRaises(ValueError):
            rf2000_eeprom.parse_matrix(sector, 80.)


# ---------------------------------------------------------------------
#  DMS probing simulation
# ---------------------------------------------------------------------
class FakeCompletion:
    def __init__(self):
        self.done = False
        self.result = None
    def test(self):
        return self.done
    def complete(self, result):
        self.done = True
        self.result = result
    def wait(self, waketime=None, waketime_result=None):
        return self.result


class FakeMutex:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class FakeReactor:
    NOW = 0.
    NEVER = 9999999999999999.
    def __init__(self):
        self.time = 100.
        self.timers = []
        self.in_timer = False
    def monotonic(self):
        return self.time
    def completion(self):
        return FakeCompletion()
    def mutex(self):
        return FakeMutex()
    def register_timer(self, cb, waketime):
        t = [cb, max(waketime, self.time)]
        self.timers.append(t)
        return t
    def unregister_timer(self, t):
        if t in self.timers:
            self.timers.remove(t)
    def advance(self, dt):
        end = self.time + dt
        if self.in_timer:
            # time passing inside a timer callback (e.g. I2C round trip)
            self.time = end
            return
        while 1:
            due = [t for t in self.timers if t[1] <= end]
            if not due:
                break
            t = min(due, key=lambda t: t[1])
            self.time = max(self.time, t[1])
            self.in_timer = True
            try:
                t[1] = t[0](self.time)
            finally:
                self.in_timer = False
        self.time = max(self.time, end)
    def pause(self, waketime):
        if waketime > self.time:
            self.advance(waketime - self.time)


class FakeBed:
    """Strain gauge model: force rises linearly once the nozzle touches."""
    def __init__(self, reactor, surface=-0.25, k=4000., noise=2,
                 idle=120, sps=15.):
        self.reactor = reactor
        self.surface = surface
        self.k = k
        self.noise = noise
        self.idle = idle
        self.sps = sps
        self.z = 5.
        self.count = 0
        self.history = []  # (time, z)
        self.last_sample_time = -1.
    def note(self):
        self.history.append((self.reactor.time, self.z))
    def z_at(self, t):
        z = self.history[0][1]
        for ht, hz in self.history:
            if ht <= t:
                z = hz
        return z
    def value_at(self, t):
        # the ADC converts continuously; a sample reflects the position at
        # the end of the previous conversion period
        period = 1. / self.sps
        sample_t = int(t / period) * period
        z = self.z_at(sample_t)
        self.count += 1
        n = (self.count * 7919) % (2 * self.noise + 1) - self.noise
        th = getattr(self, 'toolhead', None)
        if th is not None and th.queued:
            n += FakeToolhead.QUEUED_LOAD
        return int(self.idle + max(0., self.surface - z) * self.k + n), \
            sample_t


class FakeSensor:
    def __init__(self, bed):
        self.bed = bed
        self.sps = bed.sps
        self.last_value = None
        self.last_sample = None
    def get_sample_period(self):
        return 1. / self.sps
    def get_mcu(self):
        return FakeMCU()
    def read(self):
        val, st = self.bed.value_at(self.bed.reactor.time)
        fresh = st != self.last_sample
        self.last_sample = st
        self.bed.reactor.advance(0.004)  # I2C round trip
        self.last_value = val
        return val, fresh, self.bed.reactor.time
    def read_fresh(self, timeout=None):
        while 1:
            val, fresh, t = self.read()
            if fresh:
                return val, t
            self.bed.reactor.advance(0.005)


class FakeMCU:
    def estimated_print_time(self, t):
        return t
    def is_fileoutput(self):
        return False


class FakeStepper:
    def __init__(self, bed):
        self.bed = bed
    def is_active_axis(self, axis):
        return axis == 'z'
    def get_mcu(self):
        return FakeMCU()
    def get_past_mcu_position(self, print_time):
        return int(round(self.bed.z_at(print_time) / 0.0003125))
    def mcu_to_commanded_position(self, pos):
        return pos * 0.0003125
    def get_name(self):
        return 'stepper_z'
    def get_commanded_position(self):
        return self.bed.z


class FakeKin:
    def __init__(self, bed):
        self.stepper = FakeStepper(bed)
    def get_steppers(self):
        return [self.stepper]
    def calc_position(self, spos):
        return [None, None, spos['stepper_z']]


class FakeToolhead:
    DRIP_LAG = 0.15  # host only stops feeding moves -> overshoot
    # Like Klipper's lookahead: manual_move() only queues the move (the
    # commanded position changes at once, the bed moves on wait_moves()).
    # While a move is queued the gauge sees the acceleration load of the
    # hotend holder (fix 6: idle force measured during moves).
    QUEUED_LOAD = 30
    def __init__(self, bed, reactor):
        self.bed = bed
        self.reactor = reactor
        self.kin = FakeKin(bed)
        self.pos = [100., 100., bed.z, 0.]
        self.queued = []
        bed.toolhead = self
    def get_position(self):
        return list(self.pos)
    def set_position(self, pos):
        self.pos = list(pos)
    def get_kinematics(self):
        return self.kin
    def wait_moves(self):
        q, self.queued = self.queued, []
        for z, speed in q:
            self._move_z(z, speed)
    def flush_step_generation(self):
        pass
    def _move_z(self, target, speed, completion=None):
        dt = 0.002
        stop_at = None
        while abs(self.bed.z - target) > 1e-9:
            if completion is not None and completion.test():
                if stop_at is None:
                    stop_at = self.reactor.time + self.DRIP_LAG
                if self.reactor.time >= stop_at:
                    break
            step = min(speed * dt, abs(target - self.bed.z))
            self.bed.z += step if target > self.bed.z else -step
            self.bed.note()
            self.reactor.advance(dt)
        self.pos[2] = self.bed.z
    def manual_move(self, pos, speed):
        if pos[2] is not None:
            self.queued.append((pos[2], speed))
            self.pos[2] = pos[2]
        self.pos[:2] = [p if p is not None else c
                        for p, c in zip(pos[:2], self.pos[:2])]
    def drip_move(self, pos, speed, completion):
        self.wait_moves()
        self._move_z(pos[2], speed, completion)


class FakeGCmd:
    def __init__(self, params=None):
        self.params = params or {}
    def get_float(self, name, default=None, **kw):
        return float(self.params.get(name, default))
    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))
    def get(self, name, default=None):
        return self.params.get(name, default)


class FakePrinter:
    command_error = RuntimeError
    def __init__(self, toolhead):
        self.toolhead = toolhead
    def lookup_object(self, name, default=None):
        if name == 'toolhead':
            return self.toolhead
        return default


def make_dms(surface=-0.25, k=4000., speed=0.5):
    reactor = FakeReactor()
    bed = FakeBed(reactor, surface=surface, k=k)
    bed.note()
    toolhead = FakeToolhead(bed, reactor)
    dms = rf2000_dms.RF2000DMS.__new__(rf2000_dms.RF2000DMS)
    dms.printer = FakePrinter(toolhead)
    dms.reactor = reactor
    dms.sensor = FakeSensor(bed)
    dms.tare = 0
    dms.contact_delta = 10
    dms.contact_samples = 2
    dms.release_delta = 5
    dms.max_contact_digits = 5000
    dms.backoff_retries = 3
    dms.idle_samples = 5
    dms.idle_tolerance = 15
    dms.idle_retries = 5
    dms.idle_min, dms.idle_max = -7500, 7500
    dms.slow_speed = 0.05
    dms.backoff_dist = 0.15
    dms.release_dist = 0.10
    dms.settle_time = 0.15
    dms.sample_delay = 0.5 / bed.sps
    dms.z_min_position = -2.
    dms.last_idle = None
    params = {'probe_speed': speed, 'lift_speed': 5.}
    dms.get_probe_params = lambda gcmd=None: params
    return dms, bed, toolhead


class TestDMSProbe(unittest.TestCase):
    def test_contact_accuracy(self):
        for surface in (-0.4, -0.25, 0.0, 0.3):
            dms, bed, th = make_dms(surface=surface)
            th.manual_move([None, None, 1.0], 5.)
            z, idle, val = dms.probe_contact(FakeGCmd())
            # contact_delta 10 digits at 4000 digits/mm = 2.5um of pressing
            expect = surface - 10. / bed.k
            self.assertAlmostEqual(z, expect, delta=0.006,
                                   msg="surface %.3f got %.4f" % (surface, z))
            self.assertAlmostEqual(idle, bed.idle, delta=3)
            # released afterwards
            self.assertGreater(th.get_position()[2], z)

    def test_soft_gauge_still_accurate(self):
        dms, bed, th = make_dms(surface=-0.1, k=800.)
        th.manual_move([None, None, 1.0], 5.)
        z, idle, val = dms.probe_contact(FakeGCmd())
        self.assertAlmostEqual(z, -0.1 - 10. / 800., delta=0.008)

    def test_no_contact_raises(self):
        dms, bed, th = make_dms(surface=-5.)  # bed out of reach
        th.manual_move([None, None, 1.0], 5.)
        with self.assertRaises(RuntimeError):
            dms.probe_contact(FakeGCmd())

    def test_single_noise_spike_does_not_trigger(self):
        dms, bed, th = make_dms(surface=-0.3)
        th.manual_move([None, None, 1.0], 5.)
        orig = bed.value_at
        spikes = {'n': 0}
        def spiky(t):
            val, st = orig(t)
            # one isolated +40 digit spike every 9th sample
            spikes['n'] += 1
            if spikes['n'] % 9 == 0 and bed.z > 0.2:
                val += 40
            return val, st
        bed.value_at = spiky
        z, idle, val = dms.probe_contact(FakeGCmd())
        self.assertAlmostEqual(z, -0.3 - 10. / bed.k, delta=0.01)

    def test_triggered_before_movement(self):
        dms, bed, th = make_dms(surface=2.0)  # nozzle already pressed
        th.manual_move([None, None, 1.0], 5.)
        dms.idle_min, dms.idle_max = -100000, 100000
        dms.last_idle = 0.
        with self.assertRaises(RuntimeError) as cm:
            dms.descend_until_contact(1.0, bed.idle, 10)
        self.assertIn("prior to movement", str(cm.exception))

    def test_overtravel_force_limit(self):
        # very stiff gauge + fast approach -> force far above the limit
        dms, bed, th = make_dms(surface=-0.2, k=200000., speed=2.0)
        th.manual_move([None, None, 1.0], 5.)
        with self.assertRaises(RuntimeError) as cm:
            dms.probe_contact(FakeGCmd())
        self.assertIn("max_contact_digits", str(cm.exception))
        self.assertGreater(th.get_position()[2], -0.2)  # lifted again

    def test_unstable_idle_raises(self):
        dms, bed, th = make_dms()
        bed.noise = 50
        th.manual_move([None, None, 1.0], 5.)
        with self.assertRaises(RuntimeError):
            dms.measure_idle()


class TestConfigLint(unittest.TestCase):
    def test_no_jinja_whitespace_minus(self):
        # Klipper uses '{' '}' as Jinja delimiters: "{-x}" silently drops
        # the minus sign (whitespace control).  Write "{0 - x}" instead.
        for f in glob.glob(os.path.join(REPO, 'config', '**', '*.cfg'),
                           recursive=True):
            with open(f) as fh:
                for n, line in enumerate(fh, 1):
                    self.assertNotIn('{-', line, "%s:%d" % (f, n))

    def test_no_digits_in_extended_commands(self):
        # Klipper splits command names at digits - "FOO2_BAR" is unusable
        bad = []
        for f in glob.glob(os.path.join(REPO, 'config', '**', '*.cfg'),
                           recursive=True):
            with open(f) as fh:
                text = fh.read()
            for m in re.finditer(r'^\[gcode_macro (\S+)\]', text, re.M):
                name = m.group(1)
                if re.match(r'^[A-Z]\d+(\.\d+)?$', name):
                    continue  # traditional g-code like M3001
                if re.search(r'\d', name):
                    bad.append(name)
        for f in glob.glob(os.path.join(REPO, 'klippy_extras', '*.py')):
            with open(f) as fh:
                text = fh.read()
            for m in re.finditer(
                    r"register_(?:mux_)?command\(\s*['\"]([A-Z_0-9]+)['\"]",
                    text):
                name = m.group(1)
                if not re.match(r'^[A-Z]\d+$', name) and re.search(r'\d',
                                                                   name):
                    bad.append(name)
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
