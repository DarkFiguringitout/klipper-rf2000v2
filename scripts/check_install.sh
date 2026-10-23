#!/bin/bash
# Health check of a klipper-rf2000v2 installation
source "$(dirname "$0")/common.sh"
ok=1
check() { if eval "$2"; then echo "  [ OK ] $1"; else echo "  [FAIL] $1"; ok=0; fi; }

echo "klipper-rf2000v2 installation check"
check "Klipper source in $KLIPPER_DIR" "[ -f '$KLIPPER_DIR/klippy/klippy.py' ]"
check "Klippy python environment" "[ -x '$KLIPPY_ENV/bin/python' ]"
for f in "$REPO_DIR"/klippy_extras/*.py; do
    n="$(basename "$f")"
    check "extra $n linked" "[ -e '$KLIPPER_DIR/klippy/extras/$n' ]"
done
check "config rf2000v2/ present" "[ -e '$CONFIG_DIR/rf2000v2/hardware.cfg' ]"
check "printer.cfg includes rf2000v2" "grep -q 'include rf2000v2/hardware.cfg' '$CONFIG_DIR/printer.cfg' 2>/dev/null"
check "printer.cfg has [stepper_z] position_endstop (template >= v2)" "grep -qE '^(#\*# )?position_endstop' '$CONFIG_DIR/printer.cfg' 2>/dev/null"
check "printer.cfg has heater 'control:' (template >= v2)" "grep -qE '^(#\*# )?control' '$CONFIG_DIR/printer.cfg' 2>/dev/null"
check "printer.cfg serial is set" "! grep -q 'REPLACE_ME' '$CONFIG_DIR/printer.cfg' 2>/dev/null"
port="$(grep -m1 '^serial:' "$CONFIG_DIR/printer.cfg" 2>/dev/null | awk '{print $2}')"
check "serial port $port exists" "[ -n '$port' ] && [ -e '$port' ]"
check "user $USER in dialout group" "id -nG '$USER' | grep -qw dialout"
if command -v systemctl >/dev/null; then
    check "klipper service active" "systemctl is-active --quiet klipper"
    systemctl list-unit-files 2>/dev/null | grep -q moonraker.service && \
        check "moonraker service active" "systemctl is-active --quiet moonraker"
fi
log="$PRINTER_DATA/logs/klippy.log"
if [ -f "$log" ]; then
    if tail -200 "$log" | grep -q "Configured MCU 'mcu'"; then
        echo "  [ OK ] MCU connected (klippy.log)"
    else
        echo "  [INFO] MCU not (yet) connected - last messages:"
        tail -5 "$log" | sed 's/^/         /'
    fi
fi
[ $ok -eq 1 ] && info "All checks passed" || warn "Some checks failed (see above)"
[ $ok -eq 1 ]
