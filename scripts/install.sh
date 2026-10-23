#!/bin/bash
# klipper-rf2000v2 installer for a Raspberry Pi (Raspberry Pi OS / Debian)
#
#   git clone https://github.com/DarkFiguringitout/klipper-rf2000v2.git ~/klipper-rf2000v2
#   ~/klipper-rf2000v2/scripts/install.sh
#
# What it does (each step is skipped if already done):
#   1. installs Klipper (python3 venv + systemd service, KIAUH layout)
#   2. installs Moonraker and Mainsail (web interface)    [--no-web skips]
#   3. links the RF2000v2 Klipper extras into ~/klipper/klippy/extras
#   4. installs the RF2000v2 configuration into ~/printer_data/config
#   5. detects the printer's USB port and writes it into printer.cfg
#   6. builds the RF2000v2 MCU firmware and (after asking) flashes it,
#      backing up the original RF firmware first
#
# Options:
#   --rf-only     only steps 3-5 (Klipper/Moonraker already installed,
#                 e.g. MainsailOS or KIAUH)
#   --no-web      do not install Moonraker/Mainsail
#   --no-flash    do not build/flash the MCU firmware
#   --update      re-link extras after "git pull" and restart Klipper
#   -y            answer yes to all questions
set -e
source "$(dirname "$0")/common.sh"
require_not_root

RF_ONLY=0; WEB=1; FLASH=1; UPDATE=0
for a in "$@"; do
    case "$a" in
        --rf-only) RF_ONLY=1;;
        --no-web) WEB=0;;
        --no-flash) FLASH=0;;
        --update) UPDATE=1;;
        -y|--yes) ASSUME_YES=1;;
        -h|--help) sed -n 2,26p "$0"; exit 0;;
        *) die "unknown option $a";;
    esac
done

# ---------------------------------------------------------------------
install_packages() {
    info "Installing system packages"
    sudo apt-get update
    sudo apt-get install -y git python3-venv python3-dev libffi-dev \
        build-essential libncurses-dev pkg-config libusb-1.0-0 \
        gcc-avr binutils-avr avr-libc avrdude unzip curl
}

install_klipper() {
    if [ ! -d "$KLIPPER_DIR/.git" ]; then
        info "Cloning Klipper (latest master)"
        git clone https://github.com/Klipper3d/klipper.git "$KLIPPER_DIR"
    else
        info "Klipper found in $KLIPPER_DIR"
    fi
    if [ ! -x "$KLIPPY_ENV/bin/python" ]; then
        info "Creating the Klippy python environment"
        python3 -m venv "$KLIPPY_ENV"
    fi
    "$KLIPPY_ENV/bin/pip" install -q --upgrade pip
    "$KLIPPY_ENV/bin/pip" install -q -r "$KLIPPER_DIR/scripts/klippy-requirements.txt"
    mkdir -p "$PRINTER_DATA"/{config,logs,gcodes,systemd,comms}
    cat > "$PRINTER_DATA/systemd/klipper.env" <<EOF
KLIPPER_ARGS="$KLIPPER_DIR/klippy/klippy.py $CONFIG_DIR/printer.cfg -I $PRINTER_DATA/comms/klippy.serial -l $PRINTER_DATA/logs/klippy.log -a $PRINTER_DATA/comms/klippy.sock"
EOF
    if [ ! -f /etc/systemd/system/klipper.service ]; then
        info "Installing the klipper systemd service"
        sudo tee /etc/systemd/system/klipper.service >/dev/null <<EOF
[Unit]
Description=Klipper 3D Printer Firmware (RF2000v2)
Documentation=https://www.klipper3d.org/
After=network-online.target
Wants=udev.target

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=$USER
RemainAfterExit=yes
WorkingDirectory=$KLIPPER_DIR
EnvironmentFile=$PRINTER_DATA/systemd/klipper.env
ExecStart=$KLIPPY_ENV/bin/python \$KLIPPER_ARGS
Restart=always
RestartSec=10
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable klipper
    fi
    # Allow the klipper user to access the USB serial port
    sudo usermod -a -G dialout "$USER" || true
}

install_moonraker() {
    if [ ! -d "$HOME/moonraker/.git" ]; then
        info "Cloning Moonraker"
        git clone https://github.com/Arksine/moonraker.git "$HOME/moonraker"
    fi
    if [ ! -f "$CONFIG_DIR/moonraker.conf" ]; then
        sed "s|@HOME@|$HOME|g" "$REPO_DIR/config/moonraker.conf" \
            > "$CONFIG_DIR/moonraker.conf"
    fi
    if [ ! -f /etc/systemd/system/moonraker.service ]; then
        info "Running the Moonraker installer"
        "$HOME/moonraker/scripts/install-moonraker.sh" -d "$PRINTER_DATA"
    fi
}

install_mainsail() {
    if [ ! -f "$HOME/mainsail/index.html" ]; then
        info "Downloading Mainsail"
        mkdir -p "$HOME/mainsail"
        curl -fsSL -o /tmp/mainsail.zip \
            https://github.com/mainsail-crew/mainsail/releases/latest/download/mainsail.zip
        unzip -o -q /tmp/mainsail.zip -d "$HOME/mainsail"
        rm -f /tmp/mainsail.zip
    fi
    if [ ! -f /etc/nginx/sites-available/mainsail ]; then
        info "Configuring nginx for Mainsail"
        sudo apt-get install -y nginx
        sed "s|@HOME@|$HOME|g" "$REPO_DIR/config/nginx-mainsail.conf" \
            | sudo tee /etc/nginx/sites-available/mainsail >/dev/null
        sudo rm -f /etc/nginx/sites-enabled/default
        sudo ln -sf /etc/nginx/sites-available/mainsail \
            /etc/nginx/sites-enabled/mainsail
        # nginx must be able to read the web files
        chmod o+x "$HOME"
        sudo systemctl restart nginx
    fi
}

link_extras() {
    [ -d "$KLIPPER_DIR/klippy/extras" ] || die "Klipper not found in $KLIPPER_DIR"
    info "Linking RF2000v2 extras into Klipper"
    local f name
    for f in "$REPO_DIR"/klippy_extras/*.py; do
        name="$(basename "$f")"
        ln -sf "$f" "$KLIPPER_DIR/klippy/extras/$name"
        # keep Klipper's git tree clean for the Moonraker update manager
        grep -qxF "klippy/extras/$name" "$KLIPPER_DIR/.git/info/exclude" \
            2>/dev/null || echo "klippy/extras/$name" \
            >> "$KLIPPER_DIR/.git/info/exclude"
    done
}

install_config() {
    mkdir -p "$CONFIG_DIR"
    # The RF2000v2 files are linked so "git pull" updates them; personal
    # changes belong into printer.cfg.
    if [ -e "$CONFIG_DIR/rf2000v2" ] && [ ! -L "$CONFIG_DIR/rf2000v2" ]; then
        local bak="$CONFIG_DIR/rf2000v2.bak-$(date +%s)"
        warn "Existing $CONFIG_DIR/rf2000v2 moved to $bak"
        mv "$CONFIG_DIR/rf2000v2" "$bak"
    fi
    ln -sfn "$REPO_DIR/config/rf2000v2" "$CONFIG_DIR/rf2000v2"
    if [ -f "$CONFIG_DIR/printer.cfg" ] \
       && ! grep -q "include rf2000v2/hardware.cfg" "$CONFIG_DIR/printer.cfg"; then
        local bak="$CONFIG_DIR/printer.cfg.bak-$(date +%s)"
        warn "Existing printer.cfg saved as $bak"
        cp "$CONFIG_DIR/printer.cfg" "$bak"
        cp "$REPO_DIR/config/printer.cfg" "$CONFIG_DIR/printer.cfg"
    elif [ ! -f "$CONFIG_DIR/printer.cfg" ]; then
        cp "$REPO_DIR/config/printer.cfg" "$CONFIG_DIR/printer.cfg"
    fi
    info "Configuration installed in $CONFIG_DIR"
}

set_serial() {
    local port
    if port="$(find_serial_port)"; then
        info "Printer port: $port"
        sed -i "s|^serial: .*|serial: $port|" "$CONFIG_DIR/printer.cfg"
    else
        warn "No USB serial device found. Connect the RF2000v2 via USB, switch"
        warn "it on and run: scripts/install.sh --rf-only"
    fi
}

# ---------------------------------------------------------------------
if [ $UPDATE -eq 1 ]; then
    link_extras
    sudo systemctl restart klipper || true
    info "Updated. If the release notes mention firmware changes run"
    info "scripts/build_firmware.sh && scripts/flash_mcu.sh"
    exit 0
fi

info "klipper-rf2000v2 installer"
if [ $RF_ONLY -eq 0 ]; then
    install_packages
    install_klipper
    if [ $WEB -eq 1 ]; then
        install_moonraker
        install_mainsail
    fi
fi
link_extras
install_config
set_serial

if [ $FLASH -eq 1 ]; then
    "$REPO_DIR/scripts/build_firmware.sh"
    if confirm "Flash Klipper to the RF2000v2 now (original firmware is backed up first)?"; then
        "$REPO_DIR/scripts/flash_mcu.sh" -y
        set_serial
    else
        info "Flash later with: $REPO_DIR/scripts/flash_mcu.sh"
    fi
fi

sudo systemctl restart klipper || true
[ $WEB -eq 1 ] && [ $RF_ONLY -eq 0 ] && sudo systemctl restart moonraker || true
"$REPO_DIR/scripts/check_install.sh" || true
info "Done.  Open http://$(hostname -I 2>/dev/null | awk '{print $1}')/ and"
info "follow docs/04-first-start.md before the first print."
