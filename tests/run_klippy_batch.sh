#!/bin/bash
# Run the RF2000v2 configuration through Klippy in batch (simulation) mode.
#
# Usage: tests/run_klippy_batch.sh <klipper-dir> <python> [gcode files...]
#
# Builds the ATmega2560 MCU dictionary with firmware/rf2000v2.config (needs
# avr-gcc), links the klippy_extras into the Klipper tree and processes each
# G-code file.  Klippy must exit with status 0 for every file.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
KLIPPER="$(cd "${1:?klipper dir}" && pwd)"
PY="${2:?python}"
shift 2
TESTS=("$@")
[ ${#TESTS[@]} -eq 0 ] && TESTS=("$REPO"/tests/*.gcode)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export HOME="$WORK/home"
mkdir -p "$HOME/printer_data/config" "$HOME/printer_data/gcodes"

# MCU dictionary for the RF2000v2 firmware configuration
cp "$REPO/firmware/rf2000v2.config" "$WORK/kconfig"
make -C "$KLIPPER" -s KCONFIG_CONFIG="$WORK/kconfig" OUT="$WORK/out/" \
     olddefconfig >/dev/null
make -C "$KLIPPER" -s KCONFIG_CONFIG="$WORK/kconfig" OUT="$WORK/out/" \
     -j"$(nproc)" >/dev/null
DICT="$WORK/out/klipper.dict"

for f in "$REPO"/klippy_extras/*.py; do
    ln -sf "$f" "$KLIPPER/klippy/extras/$(basename "$f")"
done

# Copy the config so SAVE_CONFIG style writes never touch the repo
cp -r "$REPO/config" "$WORK/config"
# Test-only assertion macros (an error makes Klippy exit non-zero)
cat > "$WORK/config/test_asserts.cfg" <<'EOF2'
[gcode_macro ASSERT_NOT_DITTO]
gcode:
  {% if printer.extruder1.motion_queue == "extruder" %}
    {action_raise_error("ASSERT: extruder1 still synced to extruder")}
  {% endif %}

[gcode_macro ASSERT_DITTO]
gcode:
  {% if printer.extruder1.motion_queue != "extruder" %}
    {action_raise_error("ASSERT: extruder1 not synced (ditto off)")}
  {% endif %}
EOF2
sed -i '0,/^\[include rf2000v2\/hardware.cfg\]/s//[include test_asserts.cfg]\n&/' "$WORK/config/printer.cfg"
# Give the test copy a saved bed mesh profile (as SAVE_CONFIG would write)
cat >> "$WORK/config/printer.cfg" <<'EOF2'
#*# [bed_mesh default]
#*# version = 1
#*# points =
#*# 	  -0.100000, -0.050000, 0.000000, 0.050000
#*# 	  -0.080000, -0.030000, 0.020000, 0.070000
#*# 	  -0.060000, -0.010000, 0.040000, 0.090000
#*# 	  -0.040000, 0.010000, 0.060000, 0.110000
#*# x_count = 4
#*# y_count = 4
#*# mesh_x_pps = 2
#*# mesh_y_pps = 2
#*# algo = bicubic
#*# tension = 0.2
#*# min_x = 5.0
#*# max_x = 165.0
#*# min_y = 30.0
#*# max_y = 280.0
EOF2

fail=0
for t in "${TESTS[@]}"; do
    log="$WORK/$(basename "$t").log"
    outp="$WORK/$(basename "$t").stdout"
    if "$PY" "$KLIPPER/klippy/klippy.py" "$WORK/config/printer.cfg" \
         -i "$t" -o /dev/null -v -d "$DICT" -l "$log" >"$outp" 2>&1 \
       && ! grep -qE "^!! |Unknown command|Malformed command|Internal error|Error evaluating" \
            "$outp" "$log" \
       && ! grep -v "^Exception on read " "$log" | grep -A3 "Traceback" \
            | grep -vqE "Traceback|_try_read_file|open\(filename|~~~|^--$|FileNotFoundError: .*(device-tree|dmi)"; then
        echo "PASS $(basename "$t")"
    else
        echo "FAIL $(basename "$t")"
        grep -E "^!! |Unknown command|Malformed command|Internal error|Error" \
            "$outp" "$log" | head -20
        tail -20 "$log"
        fail=1
    fi
done
"$PY" "$REPO/tests/check_toolchange.py" "$KLIPPER" "$PY" "$DICT" \
    "$WORK/config" || fail=1
exit $fail
