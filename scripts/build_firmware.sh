#!/bin/bash
# Build the Klipper MCU firmware for the RF2000v2 mainboard (ATmega2560).
# Result: ~/klipper/out/klipper.elf.hex
set -e
source "$(dirname "$0")/common.sh"

[ -d "$KLIPPER_DIR" ] || die "Klipper not found in $KLIPPER_DIR (run scripts/install.sh first)"
command -v avr-gcc >/dev/null || {
    info "Installing the AVR toolchain"
    sudo apt-get install -y gcc-avr binutils-avr avr-libc avrdude
}

info "Configuring Klipper for the RF2000v2 (atmega2560, 16MHz, 250000 baud)"
cp "$REPO_DIR/firmware/rf2000v2.config" "$KLIPPER_DIR/.config"
make -C "$KLIPPER_DIR" olddefconfig >/dev/null
make -C "$KLIPPER_DIR" clean >/dev/null
info "Compiling"
make -C "$KLIPPER_DIR" -j"$(nproc)"

[ -f "$FIRMWARE_HEX" ] || die "build failed, $FIRMWARE_HEX missing"
avr-size --mcu=atmega2560 -C "$KLIPPER_DIR/out/klipper.elf" | grep -E "Program|Data"
info "Firmware ready: $FIRMWARE_HEX"
info "Flash it with: scripts/flash_mcu.sh"
