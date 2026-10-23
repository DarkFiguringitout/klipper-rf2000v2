# 6. Slicer setup

## Printer settings (all slicers)

| Setting | Value |
|---|---|
| G-code flavor | Klipper (or Marlin 2 / RepRap) |
| Bed shape | X 0..170, Y 0..290 (dual), X 0..200 when only T0 is used |
| Max height | 185 |
| Origin | front left (0,0) |
| Extruders | 2, nozzle 0.4, filament 1.75 |
| Extruder offsets | **0 / 0 for both**. Klipper applies the T1 offset itself |
| Retraction (direct drive) | 1.0 mm @ 40 mm/s as a start |
| Tool change G-code | empty, `T0`/`T1` handle the offsets and Z-hop |
| Max volumetric speed | measure with `DMS_VISCOSITY_TEST` |

Do not enter the 33.6 mm offset in the slicer as well, or it is applied
twice.

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

Optional `PRINT_START` parameters: `MESH=load|scan|none`, `PROFILE=default`,
`Z_OFFSET_SCAN=0/1`, `MONITOR=0/1` (DMS emergency monitor during the print). The defaults are in the `PRINT_START` macro
(`config/rf2000v2/macros/print.cfg`).

## End G-code

```
PRINT_END
```

## Pressure control during the print (optional)

Add to the start G-code after `PRINT_START`:

```
DMS_PRESSURE_CONTROL TARGET=3000 MODE=speed MIN_SPEED=60
```

(`TARGET` from your `DMS_VISCOSITY_TEST`). `PRINT_END` switches it off.

## Single extrusion

Use T0 only. Bed width 200 mm, `PRINT_START ... EXTRUDER1=0 TOOL=0`.
