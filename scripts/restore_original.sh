#!/bin/bash
# Restore the original RF2000v2 firmware (or any .hex) to the mainboard.
#
# Usage: scripts/restore_original.sh                 (oldest backup in ~/rf2000v2-backup)
#        scripts/restore_original.sh path/to/fw.hex  (e.g. a Conrad / community firmware)
#        scripts/restore_original.sh --eeprom        (also restore the internal EEPROM)
set -e
source "$(dirname "$0")/common.sh"

HEX=""
EEPROM=0
for a in "$@"; do
    case "$a" in
        --eeprom) EEPROM=1;;
        -y|--yes) export ASSUME_YES=1;;
        *) HEX="$a";;
    esac
done
if [ -z "$HEX" ]; then
    HEX="$(ls "$BACKUP_DIR"/flash-*.hex 2>/dev/null | sort | head -1)"
    [ -n "$HEX" ] || die "No backup found in $BACKUP_DIR - pass a .hex file"
fi
[ -f "$HEX" ] || die "$HEX not found"
PORT="$(find_serial_port)" || die "No USB serial device found"

info "Restoring $HEX to $PORT"
confirm "Continue?" || die "aborted"
stop_klipper
avrdude -p atmega2560 -c wiring -P "$PORT" -b 115200 -D -U "flash:w:$HEX:i"
if [ $EEPROM -eq 1 ]; then
    ee="${HEX/flash-/eeprom-}"
    [ -f "$ee" ] || die "$ee not found"
    avrdude -p atmega2560 -c wiring -P "$PORT" -b 115200 -U "eeprom:w:$ee:i"
fi
if systemctl is-enabled klipper >/dev/null 2>&1; then
    warn "Klipper is still enabled and would grab the serial port."
    if confirm "Disable the klipper service?"; then
        sudo systemctl disable --now klipper
    fi
fi
info "Original firmware restored."
