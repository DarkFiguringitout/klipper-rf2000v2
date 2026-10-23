#!/bin/bash
# Flash Klipper onto the RF2000v2 mainboard over USB (Arduino stk500v2
# bootloader, avrdude protocol "wiring", 115200 baud - the same way the RF
# firmware is updated).
#
# Before flashing, the current flash and the AVR internal EEPROM are read
# back and stored in ~/rf2000v2-backup so the original firmware can be
# restored with scripts/restore_original.sh.  The external EEPROM with the
# heat bed scan matrices is not touched by flashing.
#
# Usage: scripts/flash_mcu.sh [--port /dev/serial/by-id/...] [--no-backup]
#                             [--hex file.hex]
set -e
source "$(dirname "$0")/common.sh"

PORT=""
DO_BACKUP=1
HEX="$FIRMWARE_HEX"
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2;;
        --no-backup) DO_BACKUP=0; shift;;
        --hex) HEX="$2"; shift 2;;
        -y|--yes) ASSUME_YES=1; shift;;
        *) die "unknown option $1";;
    esac
done

command -v avrdude >/dev/null || sudo apt-get install -y avrdude
[ -f "$HEX" ] || die "$HEX not found - run scripts/build_firmware.sh first"
[ -n "$PORT" ] || PORT="$(find_serial_port)" \
    || die "No USB serial device found. Is the printer switched on and connected via USB?"
info "Using port $PORT"

stop_klipper
AVRDUDE=(avrdude -p atmega2560 -c wiring -P "$PORT" -b 115200)

if [ $DO_BACKUP -eq 1 ]; then
    mkdir -p "$BACKUP_DIR"
    stamp="$(date +%Y%m%d-%H%M%S)"
    if ls "$BACKUP_DIR"/flash-*.hex >/dev/null 2>&1; then
        info "A firmware backup already exists in $BACKUP_DIR - keeping it and"
        info "adding a new one (the oldest backup is your original RF firmware)"
    fi
    info "Backing up the current firmware (takes ~1-2 minutes)"
    "${AVRDUDE[@]}" -U "flash:r:$BACKUP_DIR/flash-$stamp.hex:i" \
        || die "Reading the firmware failed - nothing was written. Check the port and that the printer is on."
    "${AVRDUDE[@]}" -U "eeprom:r:$BACKUP_DIR/eeprom-$stamp.hex:i" \
        || warn "Reading the internal EEPROM failed (not fatal)"
    info "Backup stored in $BACKUP_DIR"
fi

confirm "Flash Klipper ($HEX) to the RF2000v2 now?" || die "aborted"
info "Flashing"
"${AVRDUDE[@]}" -D -U "flash:w:$HEX:i"
info "Flash complete. The printer display stays blank until Klipper connects."
start_klipper
