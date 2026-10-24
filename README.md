# klipper-rf2000v2

Run **[Klipper](https://www.klipper3d.org/)** (current mainline) on the
**Renkforce RF2000v2** with a Raspberry Pi, keeping the printer's special
hardware working: **dual extrusion**, the five **DRV8711** stepper drivers,
the **strain gauge** (DMS) for bed probing and safety, the 20x4 LCD and all
11 keys, RGB and case light, 230 V and 24 V outputs, the Z-max switch and
**milling mode**.

Klipper is not forked. The RF2000v2 support is a set of Klipper modules
(`klippy_extras/`), a machine profile (`config/`) and install / flash
scripts.

> **Status:** running on a physical RF2000v2 (Raspberry Pi 5, Raspberry Pi
> OS 13 / Debian trixie, Klipper master v0.13.0-816) since 2026-10-24.
> Every function that a sensor can confirm was tested on that printer and
> the bugs found there were fixed - see
> [08-development.md](docs/08-development.md#verified-on-a-real-rf2000v2).
> LCD, all 11 keys, RGB light and buzzer were also confirmed at the printer. Run the self-test
> ([09-hardware-validation.md](docs/09-hardware-validation.md)) on your
> printer and report the results.

## Quick start

1. **Raspberry Pi:** flash *MainsailOS* (or Raspberry Pi OS Lite) with
   Raspberry Pi Imager, enable SSH → [docs/02-raspberry-pi.md](docs/02-raspberry-pi.md)
2. Connect the RF2000v2 to the Pi with its USB cable, switch it on.
3. On the Pi:
   ```
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/DarkFiguringitout/klipper-rf2000v2.git ~/klipper-rf2000v2
   ~/klipper-rf2000v2/scripts/install.sh            # plain Raspberry Pi OS
   ~/klipper-rf2000v2/scripts/install.sh --rf-only  # MainsailOS / KIAUH
   ```
   The installer builds the RF2000v2 MCU firmware, **backs up the original
   RF firmware** and flashes Klipper over USB → [docs/03-flash-printer.md](docs/03-flash-printer.md)
4. Open `http://<pi-address>/`, run the self-test
   ([docs/09-hardware-validation.md](docs/09-hardware-validation.md)) and work through
   [docs/04-first-start.md](docs/04-first-start.md).
5. Set up the slicer → [docs/06-slicer.md](docs/06-slicer.md).

Going back is one command: `scripts/restore_original.sh`.

## What works how

| Hardware / RF feature | Klipper solution |
|---|---|
| ATmega2560 board, 78 pins | Native AVR MCU firmware, pin map verified 78/78 against both RF firmwares |
| 5x TI DRV8711 (SPI) | `drv8711.py`: Conrad register set, current control, fault input |
| Dual extrusion (T1 +33.6 mm) | `T0`/`T1` macros: offsets, mesh under the active nozzle, Z offset, alignment with the strain gauge |
| Strain gauge ADS1110 | `rf2000_dms.py`: native Klipper **probe** (mesh, accuracy, screws tilt), emergency pause/stop, SenseOffset, z-offset scan |
| Pressure / flow control | `DMS_PRESSURE_CONTROL` closed loop, RF `M3911`, viscosity test `M3939` |
| Heat bed scan matrices in EEPROM | `RF_IMPORT_HBS`: imports them as bed_mesh profiles |
| Z-max switch | `rf2000_zmax.py`: crash monitor, Z-max homing |
| Milling mode | `RF_MODE`, Z-max homing, `M3`/`M5`, find Z origin, work part scan |
| LCD 20x4 + 11 keys | `display` + `gcode_button`, RF2000v2 menu, hold-to-repeat keys, live Z |
| RGB light, case light, 230 V, 2x 24 V FET, case fan, buzzer | `led`, `output_pin`, `controller_fan`, RF light effects |
| Bed surface temperature compensation | `M140`/`M190` wrapper (optional, off like the official fw) |
| Ditto printing, RF part-fan mapping, DMS digits on the LCD | `RF_DITTO`, `M106` wrapper, `display_data` |
| RF M3xxx G-codes | compatibility macros |

Full list and command reference: [docs/05-features.md](docs/05-features.md)

## Documentation

1. [Hardware and pin map](docs/01-hardware.md)
2. [Raspberry Pi setup](docs/02-raspberry-pi.md)
3. [Flashing the printer](docs/03-flash-printer.md)
4. [First start and calibration](docs/04-first-start.md)
5. [Features and commands](docs/05-features.md)
6. [Slicer setup](docs/06-slicer.md)
7. [Troubleshooting](docs/07-troubleshooting.md)
8. [Development, verification, limitations](docs/08-development.md)
9. [Hardware validation / self-test](docs/09-hardware-validation.md) - please report your results

## Tests

```
tests/run_klippy_batch.sh ~/klipper ~/klippy-env/bin/python
KLIPPER_DIR=~/klipper ~/klippy-env/bin/python tests/test_units.py
python3 tools/verify_pins.py <RF Repetier-Firmware dir>
```

CI runs all of them against the current Klipper master and publishes the
RF2000v2 firmware `.hex` as an artifact.

## Safety

You change the firmware of a machine with heaters and 230 V. Watch the
first homing and heating. The original firmware is backed up before
flashing. Use at your own risk; there is no warranty (GPLv3).

## Credits and license

* [Klipper](https://github.com/Klipper3d/klipper) by Kevin O'Connor and contributors
* [RF1000/Repetier-Firmware](https://github.com/RF1000/Repetier-Firmware) (Conrad Electronic SE)
* [Nibbels/Repetier-Firmware](https://github.com/Nibbels/Repetier-Firmware) community firmware and the rf1000.de community

Licensed under the GNU GPL v3, like Klipper and the RF firmware ([LICENSE](LICENSE)).
Renkforce is a brand of Conrad Electronic SE. This project is not affiliated
with or endorsed by Conrad.
