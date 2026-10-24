# 4. First start and calibration

Run the self-test first ([09-hardware-validation.md](09-hardware-validation.md),
`RF_SELFTEST_ALL` and the following steps), then work through this list in
order the first time Klipper drives the printer.
Type the commands in the Mainsail console. Keep one hand near the power
switch until homing and the heaters have been checked.

## 1. Connection and drivers

```
FIRMWARE_RESTART
DRV_STATUS_ALL
```

Mainsail must show the printer as "ready". `DRV_STATUS_ALL` reads the STATUS
register of all five DRV8711 drivers. `(no faults)` is good. If readback is
not possible on your board (all values `0x000` or `0xfff`, see
[01-hardware.md](01-hardware.md)), that is not a problem: the drivers are
still configured. Check that the motors hold their position:
`SET_STEPPER_ENABLE STEPPER=stepper_x ENABLE=1` makes X hard to move by hand.

## 2. Endstops

```
QUERY_ENDSTOPS
QUERY_Z_MAX
```

All should read `open`. Press each switch by hand and query again; it must
read `TRIGGERED`.

## 3. Directions (before the first homing)

Move each axis a little with `FORCE_MOVE` and watch the direction:

```
FORCE_MOVE STEPPER=stepper_x DISTANCE=10 VELOCITY=10   ; head moves RIGHT
FORCE_MOVE STEPPER=stepper_y DISTANCE=10 VELOCITY=10   ; bed/head: Y away from the switch
FORCE_MOVE STEPPER=stepper_z DISTANCE=5 VELOCITY=5     ; bed moves DOWN
```

If one goes the wrong way, add or remove the `!` on that `dir_pin` in your
`printer.cfg` (override section), then `FIRMWARE_RESTART`.

## 4. Homing

```
G28
```

Order: X, Y, then Z to the Z-min switch, then the bed moves 5 mm down. In
mill mode Z homes to the Z-max switch first.

## 5. Heaters

Check that the temperatures shown at room temperature are plausible (about
20-25 °C) for `extruder`, `extruder1` and `heater_bed`. If a hotend reads
far off, you have the older V1/V2 hotend: put

```
[extruder]
sensor_type: RF2000 EPCOS G550
```

(and the same for `[extruder1]`) into `printer.cfg`.

Then calibrate the PID controllers and save:

```
PID_CALIBRATE HEATER=extruder TARGET=220
PID_CALIBRATE HEATER=extruder1 TARGET=220
PID_CALIBRATE HEATER=heater_bed TARGET=60
SAVE_CONFIG
```

(PID values, `[stepper_z] position_endstop` and `[rf2000_dms] z_offset` are
in your `printer.cfg`, not in the `rf2000v2/` includes, because Klipper can
only `SAVE_CONFIG` options of the main config file.)

Bed temperatures are sensor temperatures, exactly like the stock Conrad
firmware. The community firmware's surface compensation (`M140 S60` heats
the sensor to about 62 °C) can be enabled in `rf_gcodes.cfg`
(`_BED_TEMP_COMP variable_enabled: True`).

## 6. Strain gauge (DMS)

With the nozzle free (not touching anything):

```
DMS_TEST
```

The noise (range) should be well below 15 digits. Push lightly on the nozzle
from below and run `DMS_READ`: the value must change by hundreds or
thousands of digits. If it changes in the wrong direction for your
expectations it does not matter: probing uses the absolute change.

```
DMS_TARE
```

zeroes it. `PRINT_START` does that automatically at printing temperature.

## 7. Bed mesh (heat bed scan)

Either import the heat bed scan you made with the RF firmware (as long as
you have not changed the bed since):

```
RF_EEPROM_INFO
RF_IMPORT_HBS            ; active slot; slot 1 becomes profile 'default'
SAVE_CONFIG
```

> On the first tested printer the imported matrix matched a fresh strain
> gauge scan within 0.04 mm in **shape**, and its absolute heights matched
> the nozzle contact (Z0 touch within 0.005 mm). In another session, after
> the bed had been moved, the whole bed sat 0.93 mm higher. `PRINT_START`
> runs `DMS_Z_OFFSET_SCAN` by default, which measures the real contact
> height before every print and corrects such shifts. Keep `z_offset_scan`
> enabled, especially with an imported mesh.

or scan again with the strain gauge (heat the bed to printing temperature
first; the nozzle should be clean and around 150 °C, like the RF firmware's
precise scan):

```
G28
M190 S60
M109 S150
BED_MESH_CALIBRATE PROFILE=default
SAVE_CONFIG
```

`PROBE_ACCURACY` at the bed center shows how repeatable the strain gauge is
(expect a standard deviation of a few micrometers).

## 8. Z offset

> Z-max switch: Klipper assumes it sits at Z = 185 (`position_max`, as the
> RF firmware does). After `G28`, move the bed down slowly
> (`G1 Z180 F300`, then `Z184`) and check with `QUERY_Z_MAX` that the
> switch is still open. If it triggers earlier, lower `position_max` of
> `[stepper_z]` in `printer.cfg`.


The nozzle is the probe, so `z_offset` in `[rf2000_dms]` is 0 and the mesh
is in absolute heights. `DMS_Z_OFFSET_SCAN` (RF `M3900`) probes the bed
center and sets the g-code Z offset so that `Z0` touches the bed.
`PRINT_START` runs it before every print by default. For a first layer that
is too thin or too thick, adjust the offset live with the bed up/down keys
or `SET_GCODE_OFFSET Z_ADJUST=...` and make it permanent with

```
[rf2000_dms]
z_offset: 0.05      ; positive = nozzle further from the bed
```

## 9. Second extruder

```
RF_ALIGN_EXTRUDERS
```

probes the same point with T0 and T1 and stores the height difference
(`t1_z_offset`). If it reports more than 0.3 mm, level the right hotend
mechanically and run it again. The X/Y offset defaults to 33.6 / 0 mm. Print
a dual-color calibration object and correct it if needed:

```
SET_TOOL_OFFSET X=33.55 Y=-0.10
```

## 10. Extruder steps

Mark the filament 120 mm above the extruder, heat it, and extrude 100 mm
(two moves - Klipper limits single extrude-only moves to 50 mm):

```
T0
M83
G1 E50 F100
G1 E50 F100
```

new `rotation_distance = 25.0 × (extruded length / 100)`. Put the value into
`printer.cfg` for `[extruder]` / `[extruder1]`.

## 10b. Pressure advance

Per filament, with a tuning tower (Klipper's method,
https://www.klipper3d.org/Pressure_Advance.html): slice the square tower
from the Klipper docs, then before printing

```
SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY=1 ACCEL=500
TUNING_TOWER COMMAND=SET_PRESSURE_ADVANCE PARAMETER=ADVANCE START=0 FACTOR=.005
```

Pick the height with the sharpest corners, `pressure_advance = height *
0.005`, and put it into `[extruder]` / `[extruder1]` in `printer.cfg`.
Bowden-free direct drive like the RF2000v2 usually lands at 0.02-0.08.

## 11. Input shaping (needs an ADXL345, ~5 EUR)

Input shaping removes ringing/ghosting and allows higher acceleration -
the biggest print quality gain left on this machine. Klippain
[Shake&Tune](https://github.com/Frix-x/klippain-shaketune) creates the
analysis graphs.

Install Shake&Tune (dependencies, module link, Moonraker updater; no
config yet):

```
git clone https://github.com/Frix-x/klippain-shaketune.git ~/klippain_shaketune
sudo apt install -y libopenblas-dev
~/klippy-env/bin/pip install -r ~/klippain_shaketune/requirements.txt
ln -frsn ~/klippain_shaketune/shaketune ~/klipper/klippy/extras/shaketune
echo klippy/extras/shaketune >> ~/klipper/.git/info/exclude
```

plus the `[update_manager Klippain-ShakeTune]` block from the Shake&Tune
README in `moonraker.conf`. (Its `install.sh` does the same but restarts
Klipper immediately - an unsaved bed mesh would be lost.)

Then wire the sensor and enable it: follow the header of
`config/rf2000v2/optional/adxl345_shaketune.cfg` and add
`[include rf2000v2/optional/adxl345_shaketune.cfg]` to `printer.cfg`.
Do **not** add `[shaketune]` without a working accelerometer - Klipper
refuses to start without `[resonance_tester]`.

Measure (hotend cold, bed clear):

```
ACCELEROMETER_QUERY            ; must print changing values
AXES_SHAPER_CALIBRATION        ; graphs in ShakeTune_results/ (Mainsail > Machine)
```

Put the recommended shaper into `printer.cfg`, `FIRMWARE_RESTART`:

```
[input_shaper]
shaper_type_x: mzv
shaper_freq_x: 45.0
shaper_type_y: mzv
shaper_freq_y: 38.0
```

(example values - use yours). With shaping active, `max_accel` can be
raised towards the graph's suggestion; the AVR step rate still limits
speed to ~300 mm/s at 32 micro steps.

## 12. Ready

Configure your slicer ([06-slicer.md](06-slicer.md)) and start a test print.
