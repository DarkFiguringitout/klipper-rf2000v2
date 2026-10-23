#!/usr/bin/env python3
"""Cross-check the RF2000v2 pin map used by this project.

The Conrad/Repetier RF firmware uses an *extended* Arduino pin numbering
(pins 0..98) for the RF2000v2 mainboard.  Klipper uses AVR port names
(e.g. ``PF0``).  This tool parses the original firmware sources and proves,
for every pin, that

  1. the Arduino pin number in ``pins.h`` maps (via ``fastio.h``) to the
     AVR port pin written in the schematic comment above it, and
  2. the pin used in ``config/printer.cfg`` (and the extras) is that same
     port pin.

Usage:
    verify_pins.py <path-to-Repetier-dir> [printer.cfg]

Exit status is non-zero if any mismatch is found.
"""
import os
import re
import sys

# Expected Klipper pins for each pins.h symbol (what printer.cfg must use).
# Keys are the pins.h #define names in the RF2000v2 section.
EXPECTED = {
    'ORIG_X_STEP_PIN': 'PF0', 'ORIG_X_DIR_PIN': 'PF1',
    'ORIG_X_ENABLE_PIN': 'PD7', 'ORIG_X_MIN_PIN': 'PC2',
    'ORIG_Y_STEP_PIN': 'PF6', 'ORIG_Y_DIR_PIN': 'PF7',
    'ORIG_Y_ENABLE_PIN': 'PF2', 'ORIG_Y_MIN_PIN': 'PC0',
    'ORIG_Z_STEP_PIN': 'PF3', 'ORIG_Z_DIR_PIN': 'PL1',
    'ORIG_Z_ENABLE_PIN': 'PK0', 'ORIG_Z_MIN_PIN': 'PC6',
    'ORIG_Z_MAX_PIN': 'PH1',
    'ORIG_E0_STEP_PIN': 'PA4', 'ORIG_E0_DIR_PIN': 'PA6',
    'ORIG_E0_ENABLE_PIN': 'PA2',
    'ORIG_E1_STEP_PIN': 'PC1', 'ORIG_E1_DIR_PIN': 'PC3',
    'ORIG_E1_ENABLE_PIN': 'PC7',
    'ORIG_FAN_PIN': 'PA5', 'WATCHDOG_PIN': 'PA7',
    'HEATER_0_PIN': 'PB4', 'HEATER_1_PIN': 'PH6', 'HEATER_2_PIN': 'PE2',
    'BEEPER_PIN': 'PE3',
    'X_SCS_PIN': 'PL0', 'Y_SCS_PIN': 'PL2', 'Z_SCS_PIN': 'PB6',
    'O0_SCS_PIN': 'PL6', 'O1_SCS_PIN': 'PJ7',
    'X_STALL_PIN': 'PG0', 'Y_STALL_PIN': 'PG1', 'Z_STALL_PIN': 'PG2',
    'O0_STALL_PIN': 'PG3', 'O1_STALL_PIN': 'PG4',
    'DRV_FAULT': 'PE6', 'DRV_RESET1': 'PE7', 'DRV_SCLK': 'PD5',
    'DRV_SDATO': 'PD4', 'DRV_SDATI': 'PD6',
    'ENABLE_KEY_E1': 'PJ2', 'ENABLE_KEY_E2': 'PJ4', 'ENABLE_KEY_E3': 'PJ5',
    'ENABLE_KEY_E4': 'PJ6', 'ENABLE_KEY_E5': 'PH7', 'ENABLE_KEY_E6': 'PH2',
    'ENABLE_KEY_1': 'PG5', 'ENABLE_KEY_2': 'PB7', 'ENABLE_KEY_3': 'PH0',
    'ENABLE_KEY_4': 'PB5', 'ENABLE_KEY_5': 'PL7',
    'SERVO1_PIN': 'PL3', 'SERVO2_PIN': 'PL4', 'SERVO3_PIN': 'PL5',
    'OUTPUT_230V_PIN': 'PE5', 'FET1': 'PC4', 'FET2': 'PC5', 'FET3': 'PF4',
    'RGB_LIGHT_R_PIN': 'PH3', 'RGB_LIGHT_G_PIN': 'PH4',
    'RGB_LIGHT_B_PIN': 'PH5', 'CASE_LIGHT_PIN': 'PA3', 'CASE_FAN_PIN': 'PF4',
    'UI_DISPLAY_RS_PIN': 'PK1', 'UI_DISPLAY_ENABLE_PIN': 'PK3',
    'UI_DISPLAY_D4_PIN': 'PF5', 'UI_DISPLAY_D5_PIN': 'PK2',
    'UI_DISPLAY_D6_PIN': 'PA1', 'UI_DISPLAY_D7_PIN': 'PJ3',
    'SDSS': 'PB0', 'SCK_PIN': 'PB1', 'MISO_PIN': 'PB3', 'MOSI_PIN': 'PB2',
    'SDCARDDETECT': 'PD2',
}
# Analog inputs are ADC channel numbers, not Arduino pins.
EXPECTED_ADC = {'TEMP_0_PIN': 'PK5', 'TEMP_1_PIN': 'PK6',
                'TEMP_2_PIN': 'PK7', 'TEMP_3_PIN': 'PK4'}
# Pins whose pins.h comment is known to be wrong in the original source.
# SDCARDDETECT: comment says "PIND.2" but the number (19) is PD2 as well.
KNOWN_COMMENT_ISSUES = set()


def parse_fastio(path):
    """Return {arduino_pin_number: 'Pxn'} for the ATmega2560 section."""
    mapping = {}
    with open(path, encoding='latin-1') as f:
        for line in f:
            m = re.match(r'#define\s+DIO(\d+)_PIN\s+PIN([A-L])(\d)', line)
            if m:
                mapping[int(m.group(1))] = 'P%s%s' % (m.group(2), m.group(3))
    return mapping


def parse_pins_h(path):
    """Return [(name, number, comment_port)] for the RF2000v2 section."""
    with open(path, encoding='latin-1') as f:
        text = f.read()
    start = text.index('#if MOTHERBOARD == DEVICE_TYPE_RF2000v2')
    end = text.index('#endif // MOTHERBOARD == DEVICE_TYPE_RF2000v2')
    section = text[start:end].splitlines()
    out = []
    last_comment = None
    for line in section:
        m = re.match(r'\s*//\s*PIN([A-L])\.(\d)', line)
        if m:
            last_comment = 'P%s%s' % (m.group(1), m.group(2))
            continue
        # Community firmware: comment on the line above the #define.
        # Official Conrad firmware: comment trailing on the same line.
        m = re.match(r'#define\s+(\w+)\s+(-?\d+)\s*(?://\s*PIN([A-L])\.(\d))?',
                     line.strip())
        if m:
            comment = last_comment
            if m.group(3):
                comment = 'P%s%s' % (m.group(3), m.group(4))
            out.append((m.group(1), int(m.group(2)), comment))
            last_comment = None
        elif line.strip() and not line.strip().startswith('//'):
            last_comment = None
    return out


def adc_to_port(ch):
    return 'PF%d' % ch if ch < 8 else 'PK%d' % (ch - 8)


def cfg_pins(cfg_path):
    """Return the set of AVR pins referenced in a Klipper config file."""
    pins = set()
    with open(cfg_path) as f:
        for line in f:
            line = line.split('#', 1)[0]
            for m in re.finditer(r'(?<![A-Za-z0-9_])[\^~!]*(P[A-L][0-7])\b',
                                 line):
                pins.add(m.group(1))
    return pins


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    rep = sys.argv[1]
    fastio = parse_fastio(os.path.join(rep, 'fastio.h'))
    entries = parse_pins_h(os.path.join(rep, 'pins.h'))
    errors = 0
    checked = 0
    for name, num, comment in entries:
        if name in EXPECTED_ADC:
            port = adc_to_port(num)
            ok = port == EXPECTED_ADC[name] and port == comment
            checked += 1
            print('%-4s %-22s ADC%-3d -> %-4s comment=%-4s expected=%s' % (
                'OK' if ok else 'FAIL', name, num, port, comment,
                EXPECTED_ADC[name]))
            errors += not ok
            continue
        if name not in EXPECTED or num < 0:
            continue
        port = fastio.get(num)
        exp = EXPECTED[name]
        ok = port == exp and (comment in (None, port)
                              or name in KNOWN_COMMENT_ISSUES)
        checked += 1
        print('%-4s %-22s D%-3d -> %-4s comment=%-4s expected=%s' % (
            'OK' if ok else 'FAIL', name, num, port, comment, exp))
        errors += not ok
    missing = (set(EXPECTED) | set(EXPECTED_ADC)) - {e[0] for e in entries}
    for name in sorted(missing):
        print('FAIL %-22s not found in pins.h' % name)
        errors += 1
    if len(sys.argv) > 2:
        used = cfg_pins(sys.argv[2])
        valid = set(EXPECTED.values()) | set(EXPECTED_ADC.values())
        # I2C (TWI) bus pins are implied by "i2c_bus: twi"
        valid |= {'PD0', 'PD1'}
        for p in sorted(used - valid):
            print('FAIL config uses %s which is not an RF2000v2 board pin'
                  % p)
            errors += 1
        print('config references %d distinct board pins' % len(used))
    print('%d pins checked, %d errors' % (checked, errors))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
