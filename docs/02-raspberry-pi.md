# 2. Set up the Raspberry Pi

The Raspberry Pi runs the Klipper host software, Moonraker (API) and
Mainsail (web interface). The printer is connected to it with its normal
USB cable.

## What you need

* Raspberry Pi 3B, 3B+, 4, 5 or Zero 2 W (a Pi 4 with 2 GB or more is the
  most comfortable choice)
* microSD card, 16 GB or larger
* a **separate** 5 V power supply for the Pi (official PSU). Do not power
  the Pi from the printer's USB port.
* the USB cable of the RF2000v2 (USB-B on the printer side)
* a PC with [Raspberry Pi Imager](https://www.raspberrypi.com/software/)

## Option A (recommended): MainsailOS + RF-only install

MainsailOS already contains Klipper, Moonraker and Mainsail.

1. In Raspberry Pi Imager pick your Pi model, then
   **Choose OS → Other specific-purpose OS → 3D printing → Mainsail OS**.
2. Click the gear / "Edit settings": set a hostname (e.g. `rf2000`), a user
   name and password, your Wi-Fi, and **enable SSH**.
3. Write the card, put it into the Pi, power the Pi up and wait about
   3 minutes.
4. Connect to the Pi from your PC:
   ```
   ssh <user>@rf2000.local
   ```
5. Install the RF2000v2 port:
   ```
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/DarkFiguringitout/klipper-rf2000v2.git ~/klipper-rf2000v2
   ~/klipper-rf2000v2/scripts/install.sh --rf-only
   ```
   `--rf-only` keeps the Klipper, Moonraker and Mainsail that MainsailOS
   installed. It links the RF2000v2 extras into Klipper, installs the
   configuration, detects the printer's USB port, builds the RF2000v2 MCU
   firmware and asks whether to flash it. The original firmware is backed
   up first.
6. Add the update entry to `~/printer_data/config/moonraker.conf` so
   Mainsail can update the port:
   ```
   [update_manager klipper-rf2000v2]
   type: git_repo
   path: ~/klipper-rf2000v2
   origin: https://github.com/DarkFiguringitout/klipper-rf2000v2.git
   primary_branch: main
   managed_services: klipper
   ```
   Do **not** include Mainsail's `mainsail.cfg` / `client.cfg` macros: this
   project defines its own `PAUSE`, `RESUME` and `CANCEL_PRINT`, adapted to
   the RF2000v2 (two extruders, lower extruder current while paused, DMS
   emergency pause).

## Option B: plain Raspberry Pi OS Lite + full install

1. In Raspberry Pi Imager choose **Raspberry Pi OS Lite (64-bit)**, set the
   hostname, user, Wi-Fi and SSH as above, write the card and boot the Pi.
2. Then:
   ```
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/DarkFiguringitout/klipper-rf2000v2.git ~/klipper-rf2000v2
   ~/klipper-rf2000v2/scripts/install.sh
   ```
   This installs, in order: system packages and the AVR toolchain, Klipper
   (latest master, Python 3 virtualenv, `klipper.service`), Moonraker
   (official installer), Mainsail with nginx, the RF2000v2 extras and
   configuration, the MCU firmware build and the flash (with backup).

Installer options:

| Option | Effect |
|---|---|
| `--rf-only` | Only the RF2000v2 parts (Klipper already installed) |
| `--no-web` | No Moonraker / Mainsail |
| `--no-flash` | Do not build or flash the MCU firmware |
| `--update` | Re-link after `git pull` and restart Klipper |
| `-y` | Answer every question with yes |

## Files after installation

```
~/klipper/                       Klipper (unmodified mainline)
~/klipper/klippy/extras/*.py  -> links to ~/klipper-rf2000v2/klippy_extras/
~/klippy-env/                    Python environment of Klipper
~/printer_data/config/printer.cfg      your printer config (edit this one)
~/printer_data/config/rf2000v2/  -> link to ~/klipper-rf2000v2/config/rf2000v2
~/printer_data/config/rf2000v2_variables.cfg   tool offsets, modes (SAVE_VARIABLE)
~/rf2000v2-backup/flash-*.hex    backup of the original RF firmware
```

Klipper itself is not changed. The extras are added as untracked files and
listed in `.git/info/exclude`, so the Moonraker update manager does not
report Klipper as "dirty".

## Optional add-ons (tested on the RF2000v2 Pi)

### Spoolman - filament tracking

Tracks the remaining filament of every spool; Mainsail shows a spool
selector and subtracts what each print uses.

```
mkdir ~/Spoolman && cd ~/Spoolman
curl -sLO https://github.com/Donkie/Spoolman/releases/latest/download/spoolman.zip
unzip -q spoolman.zip && cp .env.example .env
bash scripts/install.sh -systemd=yes
# Moonraker only manages lower-case unit names: rename the service
sudo systemctl disable --now Spoolman
sudo mv /etc/systemd/system/Spoolman.service /etc/systemd/system/spoolman.service
sudo systemctl daemon-reload && sudo systemctl enable --now spoolman
echo spoolman >> ~/printer_data/moonraker.asvc
```

Add to `~/printer_data/config/moonraker.conf`, then `sudo systemctl restart moonraker`:

```
[spoolman]
server: http://127.0.0.1:7912
sync_rate: 5

[update_manager spoolman]
type: zip
channel: stable
repo: Donkie/Spoolman
path: ~/Spoolman
virtualenv: .venv
requirements: requirements.txt
persistent_files:
  .venv
  .env
managed_services: spoolman
```

Spoolman's web UI is at `http://<pi>:7912` (the database lives in
`~/.local/share/spoolman` and survives updates).

### Mobileraker - phone app with push notifications

Write `~/printer_data/config/mobileraker.conf` first, then the installer
runs without questions:

```
[general]
language: en
timezone: Europe/Berlin
eta_format: %%d.%%m.%%Y, %%H:%%M:%%S
include_snapshot: False

[printer RF2000]
moonraker_uri: ws://127.0.0.1:7125/websocket
moonraker_api_key: False
```

```
git clone https://github.com/Clon1998/mobileraker_companion.git ~/mobileraker_companion
~/mobileraker_companion/scripts/install.sh
```

Update manager entry for `moonraker.conf`:

```
[update_manager mobileraker]
type: git_repo
path: ~/mobileraker_companion
origin: https://github.com/Clon1998/mobileraker_companion.git
primary_branch: main
managed_services: mobileraker
env: ~/mobileraker-env/bin/python
requirements: scripts/mobileraker-requirements.txt
install_script: scripts/install.sh
```

Then install the Mobileraker app on the phone and add the printer with the
Pi's address (same Wi-Fi). Notifications are configured in the app.

Next: [3. Flash the printer](03-flash-printer.md)
