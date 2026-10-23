# 3. Flash Klipper to the RF2000v2 mainboard

The RF2000v2 mainboard has an Arduino-Mega-compatible (stk500v2) bootloader.
It is updated over the normal USB cable, the same way the RF firmware is
updated (`avrdude -patmega2560 -cwiring -b115200`). You do not need to open
the printer or use a programmer.

> **Before you start:** write down your current RF firmware settings if you
> care about them (menu → Configuration). The flash script backs up the
> complete flash and the AVR EEPROM, so you can go back at any time.
> The external EEPROM with your heat bed scan matrices is **not** erased.
> After the switch you can import those matrices into Klipper
> (`RF_IMPORT_HBS`).

## Automatic (recommended)

On the Raspberry Pi, with the printer switched on and connected via USB:

```
cd ~/klipper-rf2000v2
scripts/build_firmware.sh     # builds ~/klipper/out/klipper.elf.hex
scripts/flash_mcu.sh          # backup + flash
```

`flash_mcu.sh`:

1. finds the printer in `/dev/serial/by-id/` (and asks if there are several),
2. stops the Klipper service so the port is free,
3. reads the current flash and EEPROM into `~/rf2000v2-backup/` (1-2 min),
4. asks for confirmation and writes Klipper,
5. starts Klipper again.

The printer display stays empty until Klipper on the Pi connects. That is
normal.

## Manual

```
cd ~/klipper
cp ~/klipper-rf2000v2/firmware/rf2000v2.config .config
make olddefconfig
make
ls /dev/serial/by-id/
sudo systemctl stop klipper
# backup first
avrdude -p atmega2560 -c wiring -P /dev/serial/by-id/<port> -b 115200 -U flash:r:rf2000v2-original.hex:i
# flash
avrdude -p atmega2560 -c wiring -P /dev/serial/by-id/<port> -b 115200 -D -U flash:w:out/klipper.elf.hex:i
sudo systemctl start klipper
```

`make menuconfig` settings, if you want to configure it yourself:

| Option | Value |
|---|---|
| Enable extra low-level configuration options | yes |
| Micro-controller Architecture | Atmega AVR |
| Processor model | atmega2560 |
| Processor speed | 16 MHz |
| Communication interface | UART0 |
| Baud rate for serial port | 250000 |

All optional features (SPI, software SPI, I2C, buttons, HD44780, hardware
PWM) must stay enabled. They are on by default. The build is about 45 KB
(17 % of the flash).

## From a Windows / macOS PC instead of the Pi

Build the hex on the Pi (or download it from the CI artifacts of this
repository) and flash it with avrdude, or a GUI such as XLoader
(device: ATMega2560, baud 115200), using the same settings as above. Stop
any program that has the printer's COM port open first.

## After every Klipper update

Klipper's host and MCU software must have the same version. If Mainsail
shows "MCU protocol error" or "Version mismatch" after an update:

```
~/klipper-rf2000v2/scripts/update.sh --klipper
```

This rebuilds and flashes the firmware without making a new backup.

## Going back to the original firmware

```
~/klipper-rf2000v2/scripts/restore_original.sh            # oldest backup = original
~/klipper-rf2000v2/scripts/restore_original.sh --eeprom   # also restore the settings
~/klipper-rf2000v2/scripts/restore_original.sh some.hex   # any other firmware .hex
```

The script offers to disable the Klipper service so it no longer holds the
serial port.

## If flashing fails

* `avrdude: stk500v2_ReceiveMessage(): timeout` → wrong port, the printer is
  off, or another program (Klipper, OctoPrint, Repetier-Host) has the port
  open. `sudo systemctl stop klipper` and try again.
* Try pressing the printer's reset (switch it off and on) right before
  running the command.
* The bootloader cannot be damaged by a failed USB flash. Just run
  `flash_mcu.sh` again (or `restore_original.sh`).
* Only if the board no longer responds at all: an ISP programmer (e.g.
  USBasp) on the board's ISP header can re-install the Arduino Mega 2560
  bootloader (`stk500boot_v2_mega2560.hex`).

Next: [4. First start](04-first-start.md)
