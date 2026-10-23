#!/bin/bash
# Update klipper-rf2000v2 (and optionally Klipper itself) to the latest
# version.  Moonraker's update manager can do the same from the web UI.
set -e
source "$(dirname "$0")/common.sh"
require_not_root

info "Updating klipper-rf2000v2"
git -C "$REPO_DIR" pull --ff-only
if [ "${1:-}" = "--klipper" ]; then
    info "Updating Klipper"
    git -C "$KLIPPER_DIR" pull --ff-only
    "$KLIPPY_ENV/bin/pip" install -q -r "$KLIPPER_DIR/scripts/klippy-requirements.txt"
    info "Klipper updated - the MCU firmware must match the host version:"
    "$REPO_DIR/scripts/build_firmware.sh"
    if confirm "Flash the new MCU firmware now?"; then
        "$REPO_DIR/scripts/flash_mcu.sh" --no-backup -y
    fi
fi
"$REPO_DIR/scripts/install.sh" --update
