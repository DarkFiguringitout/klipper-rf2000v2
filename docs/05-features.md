# 5. Features and commands

Each RF firmware feature and how it is provided under Klipper.
"Native" means stock Klipper with the right configuration. "Extra" means a
module in `klippy_extras/` (loaded into unmodified Klipper). "Macro" means
G-code macros in `config/rf2000v2/macros/`.

| RF firmware feature | Klipper | How |
|---|---|---|
| 5x DRV8711 motor current / micro steps | Extra `drv8711.py` | SPI init, `SET_DRV_CURRENT`, `DRV_STATUS`, fault input |
| Motor current profiles | Macro | `RF_SILENT_MODE` (factory / community "normal"); `M3920` is a project shortcut, not an RF code |
| Reduced extruder current while paused | Macro | `PAUSE` / `RESUME` |
| Dual extrusion, T0/T1 offsets | Native + macro | `ACTIVATE_EXTRUDER`, `SET_GCODE_OFFSET`, `BED_MESH_OFFSET` |
| Align extruders (M3309) | Macro + DMS | `RF_ALIGN_EXTRUDERS` |
| T1 Z offset (M3919) | Macro | `SET_TOOL_OFFSET`, `M3919 S<um>` |
| Heat bed scan / Z compensation (M3010, M3000/M3001) | Native `bed_mesh` + Extra `rf2000_dms.py` probe | `BED_MESH_CALIBRATE` |
| Import existing HBS matrices from the EEPROM | Extra `rf2000_eeprom.py` | `RF_EEPROM_INFO`, `RF_IMPORT_HBS [SLOT=n] [PROFILE=]` (slot 1 → profile `default`, slot n → `hbs_n`, as `M3009`/`M3001`) |
| Z offset scan (M3900) | Extra | `DMS_Z_OFFSET_SCAN` |
| Static Z offset (M3006) | Native | `SET_GCODE_OFFSET Z` |
| Emergency pause on high force (M3075) | Extra | DMS monitor while printing |
| Emergency stop when Z is blocked (M3076) | Extra | DMS monitor during Z moves |
| SenseOffset (M3909) | Extra | `DMS_SENSE_OFFSET` |
| Digit flow compensation (M3911) | Extra | `DMS_FLOW_COMPENSATION` |
| **Pressure control (new)** | Extra | `DMS_PRESSURE_CONTROL` closed loop |
| SensibleViscosity test (M3939) | Extra | `DMS_VISCOSITY_TEST` |
| Filament mount/unmount by force (M3913/M3914) | Extra | `DMS_LOAD_FILAMENT`, `DMS_UNLOAD_FILAMENT` |
| Pause / continue keys, pause position (M3105) | Native `pause_resume` + macro | `PAUSE`, `RESUME` |
| Park (M3080, M3103) | Macro | `PARK` |
| Z-max switch | Extra `rf2000_zmax.py` | crash monitor, `HOME_Z_MAX`, `QUERY_Z_MAX` |
| Milling mode, find Z origin, work part scan | Macro + Extra | `RF_MODE`, `RF_FIND_Z_ORIGIN`, `RF_WORK_PART_SCAN`, `M3`/`M5` |
| 230V output (M3301) | Native `output_pin` | `SET_MAINS_OUTPUT S=1` |
| 24V FET outputs (M3300) | Native `output_pin` | `SET_FET P=1 S=1` |
| Case fan (M3120/M3121) | Native `controller_fan` | automatic |
| RGB light effects (M3303-M3308) | Native `led` + macro | `RGB_LIGHT MODE=AUTO` |
| Case light | Native `led` | `SET_LED LED=case_light WHITE=1` |
| Heat bed temperature compensation | Macro | `M140`/`M190` wrapper, off by default (the official fw table is an identity), community table selectable |
| Part fan PWM mapping (S1..254 → 90..193, S255 → full) | Macro | `M106` wrapper |
| Ditto printing (FEATURE_DITTO_PRINTING) | Native + macro | `RF_DITTO S=1/0` (`SYNC_EXTRUDER_MOTION`) |
| DMS digits on the LCD | Native `display_data` | status screen line 2, right |
| LCD 20x4 + keys | Native `display`, `gcode_button` | menu + `RF2000v2` sub menu |
| Bed up/down, extrude/retract keys | Macro | hold to repeat; live Z while printing |
| External watchdog | Native `pwm_tool` (optional) | see `hardware.cfg` |
| Optional sensor X35 (M3941) | Native `temperature_sensor` | uncomment in `hardware.cfg` |
| Driver status (M3987) | Extra | `DRV_STATUS_ALL` |

Klipper features you get on top: input shaping (with an ADXL345 on the
Pi), pressure advance, exclude object, arcs, firmware retraction, the web
interface, multiple mesh profiles, `SCREWS_TILT_CALCULATE` with the strain
gauge, and more.

---

## Strain gauge probe (`[rf2000_dms]`)

The strain gauge sits in the hotend holder. It measures the force on the
nozzle in "digits" (ADC counts). The module registers itself as Klipper's
`probe`, so all probe-based commands work: `PROBE`, `PROBE_ACCURACY`,
`PROBE_CALIBRATE`, `BED_MESH_CALIBRATE`, `SCREWS_TILT_CALCULATE`.

How a probe works:

1. measure the idle force (5 samples, spread must be < `idle_tolerance`),
2. move down continuously at `speed` (0.5 mm/s) until the force is at
   least `contact_delta` digits (10, as in the RF firmware) above idle for
   `contact_samples` (2) consecutive ADC samples, so a single noise spike
   can not trigger. A trigger before the move started is an error,
3. check that the force after the approach stays below
   `max_contact_digits` (overtravel protection), lift by `backoff_distance`
   until the force is released and measure the idle force again,
4. move down at `slow_speed` (0.05 mm/s) until contact again,
5. take the Z position at the moment the triggering ADC sample was taken,
   from the stepper history, then lift `release_distance`.

The moves use Klipper's `drip_move` (the mechanism Klipper uses for homing),
so the probe stops shortly after contact. In simulation
(`tests/test_units.py`) the measured height is within ±6 µm of the
simulated bed for soft and stiff gauges.

Extra parameters for probe commands: `CONTACT_DELTA=<digits>`,
`SLOW_SPEED=<mm/s>`, `PROBE_SPEED=<mm/s>`.

| Command | Purpose |
|---|---|
| `DMS_READ` | current force in digits |
| `DMS_TARE [SAMPLES=8] [RESET=1]` | zero the gauge (nozzle free) |
| `DMS_TEST [SAMPLES=30]` | noise statistics |
| `DMS_MONITOR ENABLE=0/1 [CLEAR=1]` | emergency monitor on/off |
| `DMS_SET_LIMITS PAUSE_MIN= PAUSE_MAX= STOP_MIN= STOP_MAX=` | emergency limits |
| `DMS_Z_OFFSET_SCAN [X= Y=] [S=100]` | probe one point, set g-code Z offset (S = learning rate %) |
| `DMS_FIND_Z_ORIGIN [CONTACT_DELTA=500]` | mill mode: tool touches work part → Z=0 |
| `DMS_SENSE_OFFSET DIGITS= [MAX_OFFSET=0.18]` | first-layer force limiter (RF M3909) |
| `DMS_FLOW_COMPENSATION MIN_DIGITS= MAX_DIGITS= EXTRUDE=% SPEED=% / OFF=1` | RF M3911 |
| `DMS_PRESSURE_CONTROL TARGET= [MODE=speed\|flow\|both] [KP= KI=] [MIN_SPEED=] [MIN_FLOW=] / OFF=1` | closed loop pressure control |
| `DMS_VISCOSITY_TEST START= END= STEP= LENGTH= MAX_DIGITS= LIMIT_DIGITS=` | force vs. flow (CSV) |
| `DMS_LOAD_FILAMENT [DIGITS=3500] [SPEED=3] [MAX_LENGTH=120]` | extrude until the nozzle is full |
| `DMS_UNLOAD_FILAMENT [DIGITS=4500] [LENGTH=90]` | soften the tip, then retract |

### Emergency functions

* **Emergency pause:** while printing, the average of 10 samples (1 s)
  outside ±15000 digits runs `PAUSE` (e.g. nozzle clogged, part knocked
  over, nozzle digging into the print).
* **Emergency stop:** when Z moves without extrusion (homing, bed moves;
  small Z changes from the bed mesh during XY moves do not count)
  and the average of 3 samples is outside ±5000 digits, Klipper shuts down
  (bed blocked).

The limits come from the RF firmware and can be changed in `dms.cfg` or with
`M3075` / `M3076` / `DMS_SET_LIMITS`.

### Pressure / flow control

Nozzle back-pressure rises when the hotend cannot melt filament as fast as
requested (too fast, too cold, partial clog). The strain gauge measures
that pressure directly. Three ways to use it:

1. **Find your limit:** `DMS_VISCOSITY_TEST START=0.5 END=6 STEP=0.25
   LIMIT_DIGITS=3000` extrudes in the air at rising speeds and prints a CSV
   (speed, mm³/s, average and peak digits). It reports the highest
   volumetric flow below `LIMIT_DIGITS`. Use that as the max volumetric speed
   in the slicer.
2. **Closed loop pressure control (new):**
   `DMS_PRESSURE_CONTROL TARGET=3000 MODE=speed`. A PI controller compares
   the filtered force with the target and lowers Klipper's speed factor
   (`M220`, mode `speed`), the extrusion factor (`M221`, mode `flow`) or both,
   but never below `MIN_SPEED` / `MIN_FLOW` and never above your own setting.
   `speed` mode is the safe default: the print slows down where the hotend
   cannot keep up and keeps the same extrusion per mm, so there is no
   under-extrusion. It only acts while extruding and above
   `flow_compensation_min_height`, and it resets at the end of the print.
   It runs in addition to pressure advance and does not change it.
3. **RF-style open loop (M3911):** reduce flow and/or speed proportionally
   between `MIN_DIGITS` and `MAX_DIGITS`, as in the community firmware.

### I2C errors

Klipper's AVR I2C driver shuts the MCU down on any bus error (timeout,
missing ACK) - there is no retry. During a print the emergency monitor reads
the strain gauge 10 times per second over the carriage cable, so a loose or
noisy connector would end the print with `i2c timeout`. If that happens on
your printer, fix the cable/connector, or print without the in-print
monitor: `PRINT_START ... MONITOR=0` (or `monitor: False` in
`[rf2000_dms]`). You then lose the emergency pause, SenseOffset, flow and
pressure control during that print; probing still works.

Only one of 2 and 3 can be active: enabling one switches the other off.
While paused the adjustments are kept (RESUME restores the g-code state
they belong to); they are removed when the print ends or is cancelled. Run `DMS_TARE` at printing temperature first
(`PRINT_START` does that).

## DRV8711 (`[drv8711 <stepper>]`)

| Command | Purpose |
|---|---|
| `SET_DRV_CURRENT STEPPER= TORQUE=1..255 [TEMPORARY=1]` | change current (TORQUE register) |
| `SET_DRV_CURRENT STEPPER= RESTORE=1` | back to the configured value |
| `SET_DRV_CURRENT STEPPER= CURRENT=<A>` | needs `sense_resistor` in the config |
| `DRV_STATUS STEPPER= [CLEAR=1]` | decode / clear the fault register |
| `DRV_STATUS_ALL [CLEAR=1]` | all five drivers |
| `DRV_DUMP STEPPER=` | read back all registers |
| `DRV_INIT STEPPER=` | send the configuration again |
| `RF_SILENT_MODE S=0/1` | factory (145/140/100/90/90) or community "normal" (110/110/105/90/90) |

Config options: `torque`, `run_current` + `sense_resistor`,
`sense_amp_gain` (5/10/20/40), `dead_time`, `reg_off`, `reg_blank`,
`reg_decay`, `reg_stall`, `reg_drive` (hex allowed), `verify_registers`,
`disable_on_shutdown` (H-bridges off when Klipper shuts down), `reset_pin`.

## Dual extrusion

* `T0` / `T1` select the extruder. The carriage moves so the new nozzle is
  at the same X/Y/Z. Near the X switch, where that is not possible (right
  after homing), the carriage moves to the nearest reachable position.
* `SET_TOOL_OFFSET X= Y= Z=` stores the T1 offsets (relative to T0).
* `RF_ALIGN_EXTRUDERS [X= Y=]` measures the T1 Z offset with the strain
  gauge.
* The bed mesh is always applied under the active nozzle
  (`BED_MESH_OFFSET`), also after `BED_MESH_PROFILE LOAD`,
  `RF_IMPORT_HBS` or a calibration while T1 is active.
  `BED_MESH_CALIBRATE` always probes with T0 and switches back afterwards.
* Each extruder has its own PID, pressure advance, rotation distance and
  current; `M104 T1 S..` / `M109 T1 S..` work as slicers expect.

## Operating mode PRINT / MILL

`RF_MODE MODE=MILL` (or the menu) switches heaters off, disables the
emergency pause and homes Z to the Z-max switch (bed away from the tool)
before X and Y. `M3`/`M5` switch the spindle output (230 V socket, change
`variable_spindle_pin` in `mill.cfg`). `RF_FIND_Z_ORIGIN` sets Z=0 on the
work part, `RF_WORK_PART_SCAN` scans the work part surface into the
`workpart` mesh (`M3141` loads it). `RF_MODE MODE=PRINT` returns to printing.

## Ditto printing

`RF_DITTO S=1` makes T1 extrude exactly like T0, so two copies are printed
33.6 mm apart (heat both nozzles). Slice a single-extruder print that fits
into X 0..136 and is narrower than about 30 mm. `RF_DITTO S=0` ends it.

## Lights and outputs

* `RGB_LIGHT MODE=OFF|WHITE|AUTO|MANUAL [R= G= B=]`. AUTO uses red while
  heating, white while printing, blue while cooling and white when idle.
  Colors via `M3303`-`M3307`.
* `SET_LED LED=case_light WHITE=0..1`, or the RIGHT key.
* `SET_MAINS_OUTPUT S=0/1` (230 V), `SET_FET P=1|2 S=0/1` (X42/X44).
* `M300 P<ms>` beeps.
* `M106 S<0..255>` uses the RF part fan mapping: S1..254 → PWM 35-76 %
  (the fan does not run reliably lower), S255 → 100 %.

## RF M-code compatibility

G-code written for the RF firmware keeps working:
`M3000 M3001 M3006 M3009 M3010 M3011 M3013 M3060 M3070 M3075 M3076 M3080
M3103 M3105 M3117 M3120 M3121 M3130 M3140 M3141 M3146 M3150 M3300 M3301
M3303-M3308 M3309 M3900 M3909 M3911 M3913 M3914 M3919 M3939 M3941
M3987 M355` (plus `M3920`, a project shortcut for `RF_SILENT_MODE`).
Not ported (no equivalent needed under Klipper): the scan tuning codes
M3020-M3055 (use the `[rf2000_dms]` / `[bed_mesh]` options), M3005,
M3028/M3029, M3079, M3090 (watchdog test), M3091 (EEPROM erase), M3115,
M3200, M3902, M3912, M3989, M3999.
