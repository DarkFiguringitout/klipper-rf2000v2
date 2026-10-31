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

## Verified on a real RF2000v2

First hardware run on 2026-10-24: RF2000v2 with V3 hotends, Raspberry Pi 5
(Raspberry Pi OS 13 / Debian trixie), Klipper master v0.13.0-816,
FTDI FT232R USB serial. Install with `scripts/install.sh -y` (original
firmware backed up, Klipper flashed and verified by avrdude). Tested
without a person at the printer, so every result below comes from a
sensor or register readback:

| Function | Result |
|---|---|
| MCU link | ready, 250000 baud, no retransmits, no watchdog resets (TPS3823 floating WDI confirmed) |
| DRV8711 SPI | all registers read back as written on all 5 drivers; UVLO latched after power-on, STD/STDLAT at rest (informational, see fix 2) |
| Thermistors | T0 17.4 / T1 19.7 / bed 19.4 C at room temperature (V3 tables correct) |
| Directions + min switches | X, Y, Z: creeping toward min triggers the switch, +5 mm releases it |
| Travel | Y 290 no lost steps; X stops at ~174.5 -> `position_max: 170` (fix 4); Z-max switch at 185.3 (185 configured), open at Z184 |
| Heaters | T0, T1, bed heat; PID_CALIBRATE T0 25.4/2.29/70.6, T1 22.9/1.96/67.1, bed 33.2/1.53/180.4 |
| Strain gauge | ADS1110 over I2C, noise range 1 digit; contact stiffness several 1000 digits/mm |
| Probe | PROBE_ACCURACY std dev 0.0042 mm, range 0.013 mm (after fix 6) |
| T1 alignment | RF_ALIGN_EXTRUDERS: T1 z offset -0.008 mm |
| Bed mesh | 3x3 BED_MESH_CALIBRATE matches the imported RF heat bed scan shape within 0.038 mm; with the RF mesh loaded the nozzle touches at g-code Z 0.00 (gauge -255 digits at Z0, none at Z0.05) |
| Z offset scan | DMS_Z_OFFSET_SCAN follows real changes: bed at 60 C +0.1 mm vs cold; a ~0.95 mm bed height change between two sessions was corrected automatically by PRINT_START |
| EEPROM | RF_EEPROM_INFO format 7, RF_IMPORT_HBS 10x15 imported |
| Print job | PRINT_START (home, mesh, z-offset scan, heat, tare, T1) -> 2-layer square -> PRINT_END from a file, no errors |
| Extrusion | DMS_VISCOSITY_TEST: back pressure 900 / 1960 digits at 0.5 / 1 mm/s; DMS_UNLOAD_FILAMENT retracts 90 mm |
| Emergency stop | driving Z into the bed shut down at -5804 digits (limit 5000) |
| Silent mode | TORQUE registers 145/100 -> 110/105 -> back (readback) |
| Outputs | FET1, FET2, 230 V socket, LEDs switch (Klipper state) |
| Part fan | spins at S255 and S128: holding T0 at 200 C the heater power rises 0.25 -> 0.415 (100 %) / 0.386 (S128 = 56 % PWM) and falls back when the fan stops |
| Keys | all 7 front keys (pause, continue, bed up/down, extrude, retract, right) logged PRESSED/RELEASED by Klipper while pressed at the printer; OK, up/down and back drive the LCD menu |
| LCD | two random codes sent with M117 were read back correctly at the printer |
| RGB light | red, green and blue channels confirmed at the printer |
| Buzzer | 3 beeps from M300 heard at the printer |
| OUT1 lamp output | the RF2000v2 has no case lamp (`FEATURE_CASE_LIGHT 0` in the official firmware); the RIGHT key now switches the RGB light instead |
| Mill mode | G28 homes Z to Z-max, M3/M5 switch the 230 V socket, back to print mode homes Z-min |
| Tool change / ditto | T1 shifts the carriage 33.6 mm and applies the T1 z offset; RF_DITTO syncs extruder1 |
| First layer height | after a failed print: full start sequence hot (bed 60 C, T0 225 C), then lowering the nozzle in 0.01 mm steps - first touch between g-code Z 0.00 and -0.01 at the bed centre and at the front edge, so a 0.20 mm first layer prints at 0.20 mm |
| Probe repeatability | 25 digits / 3 samples: std dev 0.0011-0.0016 mm at three bed positions, contact stiffness 3700-11700 digits/mm, 117-point mesh without a single false trigger |
| Pause / resume | park +45/+50/+2 mm, extruder torque 90 -> 32 while paused, resume returns to the exact position |

Bugs found on the printer and fixed: (1) `avr-size -C` missing on Debian
13, (2) DRV8711 UVLO/stall info paused every print, (3) QUERY_Z_MAX race
with the Z-max monitor shut Klipper down, (4) X travel 200 -> 170,
(5) RF PID start values far off, (6) strain gauge idle measured during
moves made every probe fail, (7) the 5 ms DRV8711 reset pulse caused
"Timer too close" on 1 of 8 restarts (now 100 ms, 16/16 restarts OK).
A first real print came out too high and did not stick. Found and fixed
on the printer: (8) the purge line ran at Y 5 but the mesh started at
Y 30, where the bed is ~0.1 mm higher - mesh_min is now Y 5; (9) the
gauge zero shifts ~30 digits after each contact (holder friction), which
made the RF fw threshold of 10 digits over 2 samples fire falsely and
failed the probe back-off - now 25 digits over 3 samples, a drift-proof
release check, and a rigid-contact check that rejects soft contacts
(plastic blob) with one automatic retry.
A code review afterwards found and fixed: the mill work part scan height
(would have driven the bed into a thick work part), probe back-off timing,
long pauses hitting idle_timeout, the idle timeout not stopping the mill
spindle, and smaller macro issues - all re-verified on the printer. Plus a PLA blob under a nozzle makes the probe
trigger early (soft force curve, ~40-200 digits/mm instead of 1000s):
clean the nozzles before probing.

First layer on the plate (print of 30 Oct): the scan in PRINT_START ran
75 s after heat-up on a cold printer and read -0.777, while rigid contacts
at the same spot later read -0.66 to -0.69 in the same homed frame. Six
0.01 mm steps of live adjust on top left about 0.03 mm under the nozzle.
The right height, judged at the printer, was offset -0.189, so PRINT_START
now soaks 180 s and the scan adds `z_offset_scan_trim` 0.15. Contacts at
225 C are not usable (586-1293 digits/mm: the nozzle sits on a soft PLA
film). The soak length is not verified from a cold start yet.

The same print made the extruder skip on the first layer: pressure control
ignored everything below `flow_compensation_min_height` (0.30 mm), so
5000-6400 digits stayed uncorrected at 2.4 mm3/s. 60 % speed and 235 C did
not help, 0.02 mm more height halved the pressure. Pressure in free air at
235 C was 3221 / 4577 / 6457 digits at 2.4 / 4.8 / 9.6 mm3/s against
1012 / 2964 / 4309 in docs/06: the nozzle was partly restricted, not
blocked. Pressure control now also runs on the first layer.

The next long print ran 10 h 20 min with the bed off from layer 12: the
target went from 60 to 0 at 23:28 while the extruder target stayed. The
G-code holds a single `M140 S60`, the console history has no bed command, so
the only route found that leaves no trace is the LCD menu's Cooldown hotbed
entry (not proven). No print fault in the log: no pause, no stall, heater
within 2 C, 100 % speed from layer 50. The plate stayed at 22 C and the
print failed somewhere before the user stopped it at layer 329 of 397.
`_RF_BED_GUARD` now checks the target every 30 s during a print. Klippy logs
no strain gauge values, so the log cannot date such a failure.

## What could not be tested without the printer

Status after the first hardware run: everything in the table above is
confirmed; the list below is kept for reference. Still open: long-print I2C reliability.


The original list from before the first hardware run, with the result
of that run in brackets:

* DRV8711 SPI timing through Klipper's software SPI at 100 kHz, and
  register readback (open-drain SDATO, see `01-hardware.md`) [confirmed]
* ADS1110 data-ready bit behaviour (with a fallback to timed sampling) and
  the real noise / stiffness of the strain gauge [confirmed: noise 1 digit]
* PID start values [were far off, replaced by measured values]
* hotend thermistor type of your printer (V3 vs. V1/V2) [V3 confirmed]
* Y travel (290 mm from the firmware), bed screw positions in
  `[screws_tilt_adjust]` (placeholders), part fan PWM frequency [Y confirmed,
  X was wrong (fix 4); screws and fan still open]
* RGB / case light polarity, buzzer type [open - needs a person]
* TPS3823 watchdog: the port relies on a floating WDI disabling it (TI
  datasheet). A pull resistor on some board revision would reset the MCU
  every ~1.6 s (see troubleshooting) [confirmed: no resets]
* the Z-max switch position (assumed at `position_max` = 185, as the RF
  firmware does; first-start step 8 checks it) [confirmed: 185.3]
* whether the part fan tolerates 100 % (the RF fw allows it for S255) [open]
* reliability of the strain gauge I2C over the carriage cable (Klipper
  shuts down on any I2C error, see docs/05) [no error in ~3 h of tests]
* the `frozen_samples` threshold (75 identical readings = 5 s at 15 SPS
  aborts a probe move as "value frozen"; raise it if a very quiet gauge
  triggers it) [no false trigger]
* DRV8711 register retention through enable (nSLEEP) cycles - the RF
  firmware relies on it as well [confirmed by readback]

Note: the Klippy batch tests run without sensor responses, so they cover
configuration, macros, tool changes and motion, but not the probing
algorithm itself; that is covered by the probe simulation in the unit
tests.

## Contributing

Run both test suites before a pull request. Keep Klipper unmodified: new
functions go into `klippy_extras/` or macros.
