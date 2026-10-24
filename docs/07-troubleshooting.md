# 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| MCU resets every ~1.6 s, "Lost communication", never gets ready | The TPS3823 hardware watchdog is active on your board (WDI not floating, e.g. a pull resistor). Enabling `[pwm_tool rf2000_watchdog]` only helps once Klipper is configured. Please open an issue with your board revision |
| After an update: `Option 'position_endstop' in section 'stepper_z' must be specified` or `Option 'control' ... must be specified` | Your printer.cfg is from an older version: copy the `[stepper_z]`, `[rf2000_dms]`, `[extruder]`, `[extruder1]` and `[heater_bed]` sections from `config/printer.cfg` of this repository into it (`scripts/check_install.sh` checks this) |
| `SAVE_CONFIG ... conflicts with included value` | The option is set in a file under `rf2000v2/`. Calibrated options (PID, `position_endstop` of Z, `z_offset`) must be in `printer.cfg` only |
| `i2c timeout` during a print | See docs/05 "I2C errors": check the carriage cable, or `PRINT_START ... MONITOR=0` |
| `mcu 'mcu': Unable to connect` | Wrong `serial:` in `printer.cfg`. `ls /dev/serial/by-id/` and put the full path there. Printer switched on? |
| `MCU Protocol error` / version mismatch | Host and MCU Klipper versions differ after an update: `scripts/update.sh --klipper` |
| Motors don't move, hold no torque | DRV8711 not configured: check `klippy.log` for `drv8711`, run `DRV_INIT STEPPER=stepper_x`, `DRV_STATUS_ALL`. Check the `enable_pin`s are not inverted. |
| A motor runs the wrong way | Toggle `!` on its `dir_pin` in `printer.cfg` |
| Motors loud / hot | `RF_SILENT_MODE S=1` or lower `torque` in `printer.cfg` |
| `DRV8711 FAULTn low without OTS/OCP/PDF ... ignored` | Normal right after power-on: the DRV8711s latch `UVLO` while the 24 V supply ramps up and pull FAULTn low when enabled. `STD`/`STDLAT` (stall detect) are always set on a motor at rest. Only OTS/AOCP/BOCP/APDF/BPDF pause a print. Seen on a real RF2000v2: the extruder drivers kept `UVLO` until the first restart, motors ran normally |
| `DRV8711 driver fault detected` | Over-current / over-temperature / under-voltage. `DRV_STATUS_ALL` names the driver and fault. Check the motor cables. `DRV_STATUS_ALL CLEAR=1` |
| Thermistor reads ~0 °C or ~300 °C | Wrong `sensor_type` for your hotend (V3 vs. V1/V2), see [04-first-start.md](04-first-start.md) step 5 |
| `ADC out of range` | A heater or sensor cable is unplugged or broken, or `[temperature_sensor X35]` is enabled without a sensor |
| `DMS idle pressure not stable` | Filament or dirt touches the nozzle, cable of the strain gauge, or the printer vibrates (fan). `DMS_TEST`, clean the nozzle, increase `idle_tolerance` slightly |
| `DMS idle pressure ... outside` | Run `DMS_TARE` with the nozzle free |
| `force ... exceeds max_contact_digits` | The fast approach pressed too far into the bed: lower `speed` in `[rf2000_dms]` |
| `DMS probe triggered prior to movement` | The nozzle already touched something when probing started (filament blob, bed too high). Clean the nozzle, start higher |
| `DMS: no contact detected` | Bed out of reach (`position_min` of Z), strain gauge not connected (`DMS_READ` changes when you push the nozzle?) |
| `i2c timeout` shutdown | Strain gauge / EEPROM I2C bus (X34) disconnected or shorted |
| Emergency pause during a print | Force > ±15000 digits: clogged nozzle, nozzle too close to the bed, part came loose. `DMS_SET_LIMITS PAUSE_MAX=` to raise the limit |
| `DMS emergency stop: Z axis blocked` | Something blocked the bed while Z was moving |
| `Z-max endstop triggered outside of homing` | The bed hit the Z-max switch: check `position_max` of Z |
| First layer too high / low | Live adjust with the bed up/down keys, then set `z_offset` in `[rf2000_dms]` |
| T1 prints offset from T0 | `SET_TOOL_OFFSET X= Y=` after a calibration print, `RF_ALIGN_EXTRUDERS` for Z |
| LCD blank | Normal until Klipper connects. Otherwise check `[display]` errors in `klippy.log` |
| Mainsail "Klipper dirty" | Run `scripts/install.sh --update` (re-adds the extras to `.git/info/exclude`) |

Logs: `~/printer_data/logs/klippy.log`. When asking for help, attach this
file.
