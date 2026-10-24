# 6. Slicer setup

## Printer settings (all slicers)

| Setting | Value |
|---|---|
| G-code flavor | Klipper (or Marlin 2 / RepRap) |
| Bed shape | X 0..170, Y 0..290 (X stops mechanically at ~174.5, measured) |
| Max height | 185 |
| Origin | front left (0,0) |
| Extruders | 2, nozzle 0.4, filament 1.75 |
| Extruder offsets | **0 / 0 for both**. Klipper applies the T1 offset itself |
| Retraction (direct drive) | 1.0 mm @ 40 mm/s as a start |
| Tool change G-code | empty, `T0`/`T1` handle the offsets and Z-hop |
| Max volumetric speed | measure with `DMS_VISCOSITY_TEST` |

Do not enter the 33.6 mm offset in the slicer as well, or it is applied
twice.

## Quality settings for Klipper

| Setting | Where (PrusaSlicer / OrcaSlicer) | Why |
|---|---|---|
| Label objects: **Firmware-specific** | Print settings > Output | Klipper `EXCLUDE_OBJECT` (skip one failed part in Mainsail) and the adaptive bed mesh |
| Arc fitting / G2-G3 | Print settings > Advanced | `[gcode_arcs]` is enabled: smaller files, smoother curves |
| Use relative E distances | Printer > General | what the macros expect (`M83`) |
| Use firmware retraction (optional) | Printer > General | retraction tunable live from Mainsail (`SET_RETRACTION`) |
| Max volumetric speed | Filament | from `DMS_VISCOSITY_TEST`; measured on the test printer (V3 hotend, PLA 210 C): flat back pressure up to 12 mm³/s, rising from 13 - use **12 mm³/s** |

Pressure advance belongs into Klipper (`docs/04` step 10b), not into the
slicer.

## Start G-code

PrusaSlicer / SuperSlicer / OrcaSlicer:

```
PRINT_START BED=[first_layer_bed_temperature] EXTRUDER=[first_layer_temperature_0] EXTRUDER1=[first_layer_temperature_1] TOOL=[initial_tool]
```

OrcaSlicer variable names: `[bed_temperature_initial_layer_single]`,
`[nozzle_temperature_initial_layer]` (one value per extruder).

Cura (two extruders):

```
PRINT_START BED={material_bed_temperature_layer_0} EXTRUDER={material_print_temperature_layer_0, 0} EXTRUDER1={material_print_temperature_layer_0, 1} TOOL={initial_extruder_nr}
```

Set an unused extruder temperature to 0 and it stays cold.

Optional `PRINT_START` parameters: `MESH=load|scan|adaptive|none`, `PROFILE=default`,
`Z_OFFSET_SCAN=0/1`, `MONITOR=0/1` (DMS emergency monitor during the print). The defaults are in the `PRINT_START` macro
(`config/rf2000v2/macros/print.cfg`).

`MESH=adaptive` (needs "Label objects") probes a fresh mesh only under the
parts at printing temperature - the best first layer, at the cost of
~25 s per probe point. The default `load` uses the saved mesh plus the
z-offset scan, which is quick and matched the real bed within 0.04 mm on
the tested printer.

## End G-code

```
PRINT_END
```

A clogged nozzle shows up in the same test: back pressure at 0.5 mm/s
(1.2 mm³/s) was 4166 digits on a partly clogged T0 and 242 digits after
four 6 mm purges at 240 C (PLA). Run
`DMS_VISCOSITY_TEST START=0.5 END=0.6 STEP=1 LENGTH=6` hot to check a nozzle;
a few hundred digits is normal.

## Pressure control during the print (optional)

Add to the start G-code after `PRINT_START`:

```
DMS_PRESSURE_CONTROL TARGET=3000 MODE=speed MIN_SPEED=60
```

(`TARGET` from your `DMS_VISCOSITY_TEST`). `PRINT_END` switches it off.

## Mainsail

`python3 ~/klipper-rf2000v2/scripts/mainsail_defaults.py` adds PLA / PETG /
ABS / TPU temperature presets (both nozzles + bed) and hides the RF M-code
aliases and internal macros from the macro panel (they keep working from
G-code; un-hide in Mainsail > Settings > Macros). Safe to run again.

The red "!" in Mainsail's top bar is the **emergency stop** (Klipper shuts
down, `FIRMWARE_RESTART` to recover).

## Single extrusion

Use T0 only. Bed width 170 mm, `PRINT_START ... EXTRUDER1=0 TOOL=0`.
