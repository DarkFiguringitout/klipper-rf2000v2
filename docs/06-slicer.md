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
| Retraction (direct drive) | **2.0 mm @ 40 mm/s**, no Z-hop (measured, see below) |
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
| Max volumetric speed | Filament | from `DMS_VISCOSITY_TEST` (see below): PLA **8 mm³/s at 225 C**, ~6 mm³/s at 210 C on the V3 hotend |

Pressure advance belongs into Klipper (`docs/04` step 10b), not into the
slicer.

## Start G-code

PrusaSlicer / SuperSlicer / OrcaSlicer:

```
PRINT_START BED=[first_layer_bed_temperature] EXTRUDER=[first_layer_temperature_0] EXTRUDER1=[first_layer_temperature_1] TOOL=[initial_tool]
```

OrcaSlicer variable names: `[bed_temperature_initial_layer_single]`,
`[nozzle_temperature_initial_layer]` (one value per extruder).

For layer progress in Mainsail / Mobileraker add, in OrcaSlicer, before
`PRINT_START`: `SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]` and
in "Layer change G-code": `SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}`.

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

## Profiles beyond the basics

Tested limits: 8 mm3/s, 1000 mm/s2, 150 mm/s travel. Layer height sets the
top speed, because a 0.28 mm layer reaches 8 mm3/s at about 60 mm/s.

| Profile | Layer | Outer / inner / infill mm/s | Main features |
|---|---|---|---|
| Speed | 0.28 | 40 / 60 / 80 | 2 walls, adaptive cubic, one wall on top, extrusion rate smoothing |
| Quality | 0.16 | 35 / 55 / 80 | ironing, scarf seam, overhang reversal, Z anti-aliasing, gyroid |
| Detail | 0.08 | 25 / 40 / 60 | as Quality, without ironing, seam at the back |

Pressure advance and input shaping stay off until they are calibrated
(docs/04 steps 10b and 11).

## Measured with the strain gauge (V3 hotend, PLA, 0.4 mm)

Flow (`DMS_VISCOSITY_TEST`, 60 s soak at temperature; avg/peak digits):

| mm³/s | 210 C | 225 C |
|---|---|---|
| 2.4 | 984 / 1105 | 1012 / 1214 |
| 4.8 | 3635 / 3821 | 2964 / 3070 |
| 7.2 | 5555 / 5764 | 4020 / 4115 |
| 8.4 | 6162 / 6406 | 4179 / 4233 |
| 9.6 | 5568 / 6963 | 4309 / 4384 |
| 12.0 | 3189 / 4016 | 3841 / 4076 |

At 210 C the extruder starts slipping above ~8.4 mm³/s (peak far above
average, then pressure collapses = under-extrusion); at 225 C pressure is a
third lower and steady to 9.6 mm³/s. That is why the stock manual
recommends 230 C for PLA. Profile: **225 C, 8 mm³/s**. Measure right after
a hotter purge and the melt zone is still hot: one earlier run showed a
flat curve and a misleading 12 mm³/s - always soak a minute first.

Retraction against ooze: extrude at 4.8 mm³/s until the pressure is
steady, retract, watch the residual nozzle pressure (PLA, 210 C):

| Retract | +0.3 s | +1 s | +3 s |
|---|---|---|---|
| 0 mm | 5294 | 4719 | 1496 |
| 0.5 / 1.0 mm | ~2250 | ~230 | ~-130 |
| 1.5 mm | 551 | 13 | 52 |
| **2.0 mm** | **-26** | -79 | -7 |
| 3.0 / 4.5 mm | relieved | | |

2.0 mm @ 40 mm/s is the shortest retraction that relieves the nozzle at
once (RF forum users settled on 1.9 mm; the Simplify3D default of 4.5 mm
is more than needed).

Flow ratio: the first real prints under-extruded at the OrcaSlicer
default of 0.98 although the nozzle pressure during printing matched the
measured flow curve (1000-2200 digits at 1-3 mm³/s, so no extruder slip
and no clog). 110 % live extrusion (M221) looked right, so the PLA
profile uses **1.08**. E steps are the stock RF values (8 full steps/mm,
`rotation_distance: 25`); whether the difference is the extruder gear or
the filament diameter needs the ruler check (docs/04 step 10).

Live correction: the PLA filament start G-code enables
`DMS_PRESSURE_CONTROL TARGET=4500 MODE=speed MIN_SPEED=50`: above 4500
digits (normal printing stays below ~4300 at 225 C, the extruder slips
from ~6200) the print slows down until the hotend keeps up again.

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
