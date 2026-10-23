#!/usr/bin/env python3
"""Convert the RF firmware (Repetier) thermistor tables into Klipper
``[thermistor]`` definitions.

The RF2000v2 firmware stores thermistor curves as (10bit-ADC*4, degC*8)
pairs measured through a 4.7k pull-up to 5V (AVR AREF = AVCC).  Klipper
needs either a beta value or three (temperature, resistance) points for the
Steinhart-Hart equation.  This tool:

  * converts every table row to a thermistor resistance,
  * fits Steinhart-Hart coefficients through three representative points,
  * reports the worst-case deviation of the fit against the original table
    so the generated config can be trusted,
  * prints a ready to paste ``[thermistor ...]`` section.

Usage: thermistor_from_repetier.py <Extruder.cpp> [table_number ...]
"""
import math
import re
import sys

PULLUP = 4700.
ADC_MAX = 1023.


def parse_tables(path):
    with open(path, encoding='latin-1') as f:
        text = f.read()
    tables = {}
    # Only parse tables that are not inside /* */ comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    for m in re.finditer(
            r'const short temptable_(\d+)\[NUMTEMPS_\d+\]\[2\] PROGMEM = '
            r'\{(.*?)\};', text, re.S):
        rows = re.findall(r'\{\s*(-?\d+)\s*\*\s*4\s*,\s*(-?\d+)\s*\*\s*8\s*\}',
                          m.group(2))
        if rows:
            tables[int(m.group(1))] = [(int(a), int(t)) for a, t in rows]
    return tables


def adc_to_resistance(adc):
    adc = min(max(adc, 0.5), ADC_MAX - 0.5)
    return PULLUP * adc / (ADC_MAX - adc)


def fit_steinhart_hart(points):
    """points: [(temp_c, resistance)] x3 -> (a, b, c)"""
    (t1, r1), (t2, r2), (t3, r3) = points
    l1, l2, l3 = math.log(r1), math.log(r2), math.log(r3)
    y1, y2, y3 = [1. / (t + 273.15) for t in (t1, t2, t3)]
    g2 = (y2 - y1) / (l2 - l1)
    g3 = (y3 - y1) / (l3 - l1)
    c = (g3 - g2) / (l3 - l2) / (l1 + l2 + l3)
    b = g2 - c * (l1 * l1 + l1 * l2 + l2 * l2)
    a = y1 - (b + l1 * l1 * c) * l1
    return a, b, c


def sh_temp(coef, r):
    a, b, c = coef
    ln = math.log(r)
    return 1. / (a + b * ln + c * ln ** 3) - 273.15


def nearest(rows, temp):
    return min(rows, key=lambda row: abs(row[1] - temp))


def analyse(num, table, lo=20, hi=290):
    rows = [(adc, t) for adc, t in table if 0 < adc < ADC_MAX and lo <= t <= hi]
    pick = [nearest(rows, t) for t in (25, 150, 250)]
    pts = [(t, adc_to_resistance(adc)) for adc, t in pick]
    coef = fit_steinhart_hart(pts)
    worst = 0.
    for adc, t in rows:
        err = sh_temp(coef, adc_to_resistance(adc)) - t
        worst = max(worst, abs(err))
    return pts, coef, worst, rows


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    tables = parse_tables(sys.argv[1])
    wanted = [int(x) for x in sys.argv[2:]] or [3, 13, 14]
    names = {3: 'RF2000 Bed EPCOS G550', 13: 'RF2000 Conrad V3 Hotend',
             14: 'RF2000 NTC 3950 X35'}
    for num in wanted:
        if num not in tables:
            print('# table %d not found' % num)
            continue
        pts, coef, worst, rows = analyse(num, tables[num])
        print('# Repetier table %d: %d rows, Steinhart-Hart worst error'
              ' %.2f C over %d..%d C' % (num, len(tables[num]), worst,
                                        rows[-1][1], rows[0][1]))
        print('[thermistor %s]' % names.get(num, 'table %d' % num))
        for i, (t, r) in enumerate(pts, 1):
            print('temperature%d: %d' % (i, t))
            print('resistance%d: %.0f' % (i, r))
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
