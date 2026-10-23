#!/bin/bash
# Shared helpers for the klipper-rf2000v2 scripts
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KLIPPER_DIR="${KLIPPER_DIR:-$HOME/klipper}"
KLIPPY_ENV="${KLIPPY_ENV:-$HOME/klippy-env}"
PRINTER_DATA="${PRINTER_DATA:-$HOME/printer_data}"
CONFIG_DIR="$PRINTER_DATA/config"
BACKUP_DIR="${BACKUP_DIR:-$HOME/rf2000v2-backup}"
FIRMWARE_HEX="$KLIPPER_DIR/out/klipper.elf.hex"

c_green=$'\e[32m'; c_yellow=$'\e[33m'; c_red=$'\e[31m'; c_off=$'\e[0m'
info()  { echo "${c_green}==>${c_off} $*"; }
warn()  { echo "${c_yellow}WARNING:${c_off} $*" >&2; }
die()   { echo "${c_red}ERROR:${c_off} $*" >&2; exit 1; }

confirm() {
    # confirm "question" -> returns 0 for yes.  ASSUME_YES=1 skips prompts.
    [ "${ASSUME_YES:-0}" = "1" ] && return 0
    local reply
    read -r -p "$1 [y/N] " reply
    [[ "$reply" =~ ^[YyJj] ]]
}

require_not_root() {
    [ "$(id -u)" -eq 0 ] && die "Run this script as your normal user (it uses sudo where needed), not as root."
    return 0
}

# Find the RF2000v2 USB serial port.  The board uses a USB-serial bridge
# (FTDI FT232R or CH340 depending on the production batch).
find_serial_port() {
    local ports=()
    local p
    for p in /dev/serial/by-id/*; do
        [ -e "$p" ] && ports+=("$p")
    done
    if [ ${#ports[@]} -eq 0 ]; then
        return 1
    elif [ ${#ports[@]} -eq 1 ]; then
        echo "${ports[0]}"
    else
        echo "Several serial devices found:" >&2
        local i=1
        for p in "${ports[@]}"; do echo "  $i) $p" >&2; i=$((i+1)); done
        local n
        read -r -p "Number of the RF2000v2 port: " n
        [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le ${#ports[@]} ] \
            || die "invalid selection"
        echo "${ports[$((n-1))]}"
    fi
}

stop_klipper() {
    if systemctl list-units --full -all 2>/dev/null | grep -q "klipper.service"; then
        info "Stopping klipper service"
        sudo systemctl stop klipper
    fi
}

start_klipper() {
    if systemctl list-units --full -all 2>/dev/null | grep -q "klipper.service"; then
        info "Starting klipper service"
        sudo systemctl start klipper
    fi
}
