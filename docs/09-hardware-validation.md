# 9. Hardware validation (please report your results)

This port was verified against the firmware sources, the datasheets and in
Klipper's simulation, but it has not yet run on a physical RF2000v2 (see
[08-development.md](08-development.md)). The self-test macros below take
about 15 minutes on the printer and check every hardware function in turn.
Each step prints what you should see or hear (`SELFTEST ...` lines in the
console).

Run them right after flashing ([03-flash-printer.md](03-flash-printer.md)),
**before** the first print. Keep a hand near the power switch for steps 6-8.

| Step | Command | Checks | Moves / heats |
|---|---|---|---|
| 1 | `RF_SELFTEST_DRIVERS` | all 5 DRV8711 configured, no faults, register readback | no |
| 2 | `RF_SELFTEST_SENSORS` | both hotend and the bed thermistors read room temperature | no |
| 3 | `RF_SELFTEST_ENDSTOPS` | X/Y/Z-min, Z-max and the driver fault line (press each switch, run again) | no |
| 4 | `RF_SELFTEST_DMS` | strain gauge noise, reaction to pushing the nozzle | no |
| 5 | `RF_SELFTEST_OUTPUTS` | RGB, case light, buzzer, part fan 50/100 %, FET1, FET2, 230 V; then press every front key | no |
| 6 | `RF_SELFTEST_MOTION` | homing, X/Y/Z directions, T0/T1 carriage shift, Z-max switch open | **yes** |
| 7 | `RF_SELFTEST_HEATERS` | T0, T1 and bed heat up | **heats** |
| 8 | `RF_SELFTEST_PROBE` | probe repeatability (`PROBE_ACCURACY`), T1 height (`RF_ALIGN_EXTRUDERS`) | **yes** |

`RF_SELFTEST_ALL` runs steps 1-4 at once.

Then continue with the calibration in [04-first-start.md](04-first-start.md)
(PID, bed mesh, E-steps, first test print).

## What to do if a step fails

| Step | Likely cause | Fix |
|---|---|---|
| 1 faults / motors limp | DRV8711 not programmed | `DRV_INIT STEPPER=...`, check `klippy.log`; report the `DRV_DUMP` output |
| 2 far from room temp | other hotend version | `sensor_type: RF2000 EPCOS G550` for that extruder in `printer.cfg` |
| 3 always TRIGGERED / never | switch polarity | toggle `!` on that `endstop_pin` |
| 4 large noise | cable, fan vibration | `DMS_TEST` with fans off, check the hotend cable |
| 5 fan too weak at 50 % | fan mapping | adjust `variable_pwm_min/max` of `M106` in `macros/print.cfg` |
| 6 wrong direction | dir polarity | toggle `!` on that `dir_pin` |
| 8 large deviation | probe parameters | report the `PROBE_ACCURACY` and `DMS_TEST` output |
| MCU resets every ~1.6 s | hardware watchdog active | see [07-troubleshooting.md](07-troubleshooting.md) |

## Report your results

Please open an issue with the **Hardware test report** template
(GitHub → Issues → New issue) and paste the console output of each step and
`~/printer_data/logs/klippy.log`. Every report turns an "unverified" item in
docs/08 into a verified one, or gets a fix.
