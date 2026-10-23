# 8. Development, verification and limitations

## Repository layout

```
klippy_extras/     Klipper modules (linked into ~/klipper/klippy/extras)
  drv8711.py         TI DRV8711 SPI driver
  rf2000_dms.py      strain gauge: probe, emergency, pressure control, ...
  rf2000_eeprom.py   RF EEPROM reader, HBS -> bed_mesh import
  rf2000_zmax.py     Z-max switch: crash monitor + Z-max homing
config/            printer.cfg, rf2000v2/*.cfg, moonraker/nginx templates
firmware/          Klipper MCU build configuration for the RF2000v2
scripts/           install, build, flash, restore, update, check
tools/             verify_pins.py, thermistor_from_repetier.py
tests/             unit tests, Klippy batch-mode tests, position checks
```

## Sources

* Official Conrad RF firmware: <https://github.com/RF1000/Repetier-Firmware>
  (branch `development`, folder `RF2000v2`)
* Community firmware: <https://github.com/Nibbels/Repetier-Firmware>
* Klipper: <https://github.com/Klipper3d/klipper> (mainline, not forked)
* TI DRV8711, ADS1110 and TPS3823 datasheets

## How it was verified

Everything below runs in CI (`.github/workflows/ci.yml`) against the
**current Klipper master**:

1. **Pin map:** `tools/verify_pins.py` against the official and the
   community firmware: 78/78 pins agree (Arduino number → `fastio.h` port →
   schematic comment → Klipper config).
2. **Thermistors:** `tools/thermistor_from_repetier.py` fits the Klipper
   Steinhart-Hart coefficients from the RF tables. Worst-case error is
   below 1.8 °C between 20 and 290 °C, which is the resolution of the
   10-bit tables.
3. **MCU firmware:** Klipper is compiled for the ATmega2560 with
   `firmware/rf2000v2.config` (about 45 KB flash, 17 %). The hex file is a
   CI artifact.
4. **Unit tests** (`tests/test_units.py`):
   * every DRV8711 SPI frame equals the Conrad register constants
     (CTRL for 4..64 micro steps, OFF, BLANK, DECAY, STALL, DRIVE, STATUS,
     TORQUE `0x1100+level`),
   * RF EEPROM matrix parsing and resampling (plane survives exactly),
   * strain gauge probing against a simulated bed + gauge with a 15 SPS
     ADC, I2C latency and drip-move overshoot: within ±6 µm,
     no-contact and unstable-idle errors,
   * lint: no `{-` in Jinja templates and no digits in extended command
     names (both silently break in Klipper; both bugs were found and fixed
     during development).
5. **Klippy batch mode** (`tests/run_klippy_batch.sh`): the complete
   configuration is loaded by real Klippy with the RF2000v2 MCU dictionary
   and G-code files are processed: a dual extrusion print (start, tool
   changes while extruding, pause/resume, end), a dual print with a loaded
   bed mesh, mill mode with Z-max homing, and every RF M-code alias. Any
   `!!` error, unknown or malformed command fails the test.
6. **Tool change positions** (`tests/check_toolchange.py`): the commanded
   carriage position is asserted after each step (T1 right after homing,
   T0↔T1, changed T1 offsets with Z), to 0.01 mm.

```
tests/run_klippy_batch.sh ~/klipper ~/klippy-env/bin/python
KLIPPER_DIR=~/klipper ~/klippy-env/bin/python tests/test_units.py
```

## What could not be tested without the printer

The port was built from the firmware sources and datasheets and verified
in simulation. It has **not yet run on a physical RF2000v2**. These points
need confirming on real hardware; please report results in an issue:

* DRV8711 SPI timing through Klipper's software SPI at 100 kHz, and
  register readback (open-drain SDATO, see `01-hardware.md`)
* ADS1110 data-ready bit behaviour (with a fallback to timed sampling) and
  the real noise / stiffness of the strain gauge
* PID start values (Repetier values; run `PID_CALIBRATE` anyway)
* hotend thermistor type of your printer (V3 vs. V1/V2)
* Y travel (290 mm from the firmware), bed screw positions in
  `[screws_tilt_adjust]` (placeholders), part fan PWM frequency
* RGB / case light polarity, buzzer type
* TPS3823 watchdog: the port relies on a floating WDI disabling it (TI
  datasheet). A pull resistor on some board revision would reset the MCU
  every ~1.6 s (see troubleshooting)
* the Z-max switch position (assumed at `position_max` = 185, as the RF
  firmware does; first-start step 8 checks it)
* whether the part fan tolerates 100 % (the RF fw allows it for S255)
* reliability of the strain gauge I2C over the carriage cable (Klipper
  shuts down on any I2C error, see docs/05)
* the `frozen_samples` threshold (75 identical readings = 5 s at 15 SPS
  aborts a probe move as "value frozen"; raise it if a very quiet gauge
  triggers it)
* DRV8711 register retention through enable (nSLEEP) cycles - the RF
  firmware relies on it as well

Note: the Klippy batch tests run without sensor responses, so they cover
configuration, macros, tool changes and motion, but not the probing
algorithm itself; that is covered by the probe simulation in the unit
tests.

## Contributing

Run both test suites before a pull request. Keep Klipper unmodified: new
functions go into `klippy_extras/` or macros.
