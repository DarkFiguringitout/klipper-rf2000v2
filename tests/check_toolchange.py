#!/usr/bin/env python3
"""Run a tool change sequence in Klippy batch mode and assert the real
carriage (kinematic) X/Y positions for T0 and T1.

Usage: check_toolchange.py <klipper-dir> <python> <dict> <config-dir>
"""
import os
import re
import subprocess
import sys
import tempfile

GCODE = """G28
T1
GET_POSITION
T0
GET_POSITION
G1 X100 Y50 Z1 F6000
GET_POSITION
T1
GET_POSITION
G1 X50 Y80 F6000
GET_POSITION
SET_TOOL_OFFSET X=33.0 Y=0.5 Z=0.1
GET_POSITION
G1 X40 Y40 F6000
GET_POSITION
T0
GET_POSITION
"""
# expected commanded carriage (toolhead) X, Y, Z after each GET_POSITION.
# T1 right after homing (carriage clamped at the X switch), then T1 is
# 33.6mm right of T0 (default), then re-calibrated to X33 Y0.5 Z0.1
EXPECT = [(-33.6, 0.0, 5.0), (0.0, 0.0, 5.0),
          (100.0, 50.0, 1.0), (66.4, 50.0, 1.0), (16.4, 80.0, 1.0),
          (17.0, 79.5, 1.1), (7.0, 39.5, 1.1), (40.0, 40.0, 1.0)]


def main():
    klipper, py, dictf, cfgdir = sys.argv[1:5]
    with tempfile.TemporaryDirectory() as tmp:
        g = os.path.join(tmp, "t.gcode")
        log = os.path.join(tmp, "k.log")
        with open(g, "w") as f:
            f.write(GCODE)
        env = dict(os.environ, HOME=os.path.join(tmp, "h"))
        for d in ("printer_data/config", "printer_data/gcodes"):
            os.makedirs(os.path.join(env["HOME"], d))
        subprocess.run([py, os.path.join(klipper, "klippy", "klippy.py"),
                        os.path.join(cfgdir, "printer.cfg"), "-i", g,
                        "-o", "/dev/null", "-v", "-d", dictf, "-l", log],
                       env=env, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        with open(log) as f:
            text = f.read()
    got = [tuple(float(v) for v in m)
           for m in re.findall(r"^toolhead: X:(\S+) Y:(\S+) Z:(\S+)",
                               text, re.M)]
    ok = len(got) == len(EXPECT)
    for i, (g_, e) in enumerate(zip(got, EXPECT)):
        good = all(abs(a - b) < 0.01 for a, b in zip(g_, e))
        ok = ok and good
        print("%s step %d: carriage %s expected %s" % (
            "OK  " if good else "FAIL", i, g_, e))
    if len(got) != len(EXPECT):
        print("FAIL: got %d positions, expected %d" % (len(got), len(EXPECT)))
    print("PASS toolchange positions" if ok else "FAIL toolchange positions")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
