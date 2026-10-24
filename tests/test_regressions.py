#!/usr/bin/env python3
"""Regression checks for the bugs found on the first real RF2000v2.

Run: KLIPPER_DIR=~/klipper python3 tests/test_regressions.py
The probe model in test_units.py (queued manual_move, +30 digits while a
move is queued) covers the strain gauge wait_moves() fixes.
"""
import os, re, sys, glob, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KLIPPER = os.path.expanduser(os.environ.get('KLIPPER_DIR', '~/klipper'))
sys.path.insert(0, os.path.join(KLIPPER, 'klippy'))
sys.path.insert(0, os.path.join(REPO, 'tests'))
import test_units as tu                                   # noqa: E402
from extras import drv8711, rf2000_zmax       # noqa: E402


# --- fix 3: every Z-max endstop query must hold the query mutex -----------
class HeldMutex:
    def __init__(self):
        self.held = 0
    def __enter__(self):
        assert self.held == 0, "re-entered query mutex"
        self.held += 1
    def __exit__(self, *a):
        self.held -= 1
        return False


class TestFix3ZMaxMutex(unittest.TestCase):
    def make(self):
        z = rf2000_zmax.RF2000ZMax.__new__(rf2000_zmax.RF2000ZMax)
        z.query_mutex = m = HeldMutex()
        z.printer = type('P', (), {'is_shutdown': lambda s: False,
                                   'lookup_object': lambda s, n: th})()
        z.reactor = tu.FakeReactor()
        z.homing_active = z.in_home_cmd = False
        z.hits = 0
        z.monitor_interval = 0.5
        seen = []
        class EP:
            def query_endstop(self, pt):
                seen.append(m.held)       # 1 if the mutex is held
                return 0
            def get_mcu(self):
                return tu.FakeMCU()
        z.mcu_endstop = EP()
        th = type('T', (), {
            'get_last_move_time': lambda s: 1.0,
            'get_status': lambda s, t: {'homed_axes': 'xyz'}})()
        return z, seen

    def test_query_paths_hold_mutex(self):
        z, seen = self.make()
        z._monitor(100.)
        z._query()
        self.assertEqual(seen, [1, 1])


# --- fix 2: info bits are not faults in get_status() -----------------------
class TestFix2FaultStatus(unittest.TestCase):
    def test_get_status_faults(self):
        d = drv8711.DRV8711.__new__(drv8711.DRV8711)
        d.torque = d.run_torque = 90
        d.microsteps = 32
        d.sense_resistor = None
        d.initialized = True
        d.last_status = 0x0e0           # UVLO STD STDLAT
        self.assertEqual(d.get_status()['faults'], [])
        d.last_status = 0x0e3
        self.assertEqual(d.get_status()['faults'], ['OTS', 'AOCP'])


# --- fix 1/4/5: configuration and script guards ---------------------------
def section(text, name):
    m = re.search(r'^\[%s\]\n(.*?)(?=^\[|\Z)' % re.escape(name), text,
                  re.M | re.S)
    return m.group(1) if m else ''


class TestConfigGuards(unittest.TestCase):
    def test_measured_travel(self):
        hw = open(os.path.join(REPO, 'config/rf2000v2/hardware.cfg')).read()
        self.assertRegex(section(hw, 'stepper_x'), r'(?m)^position_max: 170\b',
                         "X travel measured at ~174.5 mm")
        self.assertRegex(section(hw, 'stepper_y'), r'(?m)^position_max: 290\b')

    def test_pid_in_printer_cfg_only(self):
        pc = open(os.path.join(REPO, 'config/printer.cfg')).read()
        hw = open(os.path.join(REPO, 'config/rf2000v2/hardware.cfg')).read()
        for h in ('extruder', 'extruder1', 'heater_bed'):
            s = section(pc, h)
            self.assertRegex(s, r'(?m)^control: pid', h)
            kp = float(re.search(r'^pid_Kp: ([\d.]+)', s, re.M).group(1))
            self.assertLess(kp, 60., "%s pid_Kp looks like the RF fw value" % h)
            self.assertNotRegex(section(hw, h), r'(?m)^(control|pid_K[pid]):', h)

    def test_build_script_portable_avr_size(self):
        s = open(os.path.join(REPO, 'scripts/build_firmware.sh')).read()
        self.assertIn('avr-size --format=avr', s)
        self.assertNotRegex(s, r'avr-size\s+-C\b', "-C is not in Debian 13 binutils")


if __name__ == '__main__':
    unittest.main(verbosity=2)
