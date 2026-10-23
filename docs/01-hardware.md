# 1. RF2000v2 hardware and how Klipper uses it

The Renkforce RF2000v2 has one mainboard (Conrad, ATmega2560 @ 16 MHz).
Klipper's firmware runs on that board. Everything else (motion planning,
G-code, macros, web UI) runs on the Raspberry Pi.

```
 Raspberry Pi (Klipper host, Moonraker, Mainsail)
        |
        |  USB cable (USB-serial bridge on the RF board, 250000 baud)
        v
 RF2000v2 mainboard  ATmega2560 (Klipper MCU firmware)
   |-- 5x TI DRV8711 stepper pre-drivers   (software SPI, active-high CS)
   |-- ADS1110 strain gauge ADC "DMS"      (I2C 0x49, hotend force sensor)
   |-- 24C256 EEPROM, heat bed matrices    (I2C 0x50)
   |-- 24C256 EEPROM, board type           (I2C 0x51)
   |-- TPS3823 external watchdog           (WDI on PA7)
   |-- HD44780 20x4 LCD + 11 keys
   |-- 2 hotends, heated bed, 4 thermistor inputs
   |-- part fan, case fan (FET3), 2 more 24V FETs, 230V relay, RGB, case light
```

## How the pin map was verified

The RF firmware uses its own extended Arduino pin numbers (0-98) for this
board. `tools/verify_pins.py` parses `pins.h` and `fastio.h` of the original
firmware and checks, for every pin, that

1. the Arduino number maps to the AVR port in `fastio.h`,
2. that port matches the schematic comment in `pins.h` (e.g. `PINF.0, 97, STP_DRV1`),
3. that port is the one used in this Klipper configuration.

Result against both the **official Conrad firmware** (RF1000/Repetier-Firmware,
branch `development`, `RF2000v2/`) and the **community firmware**
(Nibbels/Repetier-Firmware): **78 of 78 pins match.** The check runs in CI.

```
python3 tools/verify_pins.py <Repetier dir> [config/rf2000v2/hardware.cfg]
```

## Pin map

### Motion

| Function | AVR pin | RF fw name | Klipper |
|---|---|---|---|
| X step / dir / enable | PF0 / PF1 / PD7 | STP/DIR/ENA_DRV1 | `[stepper_x]`, dir inverted |
| Y step / dir / enable | PF6 / PF7 / PF2 | STP/DIR/ENA_DRV2 | `[stepper_y]` |
| Z step / dir / enable | PF3 / PL1 / PK0 | STP/DIR/ENA_DRV3 | `[stepper_z]` |
| E0 (left) step / dir / enable | PA4 / PA6 / PA2 | STP/DIR/ENA_DRV4 | `[extruder]` |
| E1 (right) step / dir / enable | PC1 / PC3 / PC7 | STP/DIR/ENA_DRV5 | `[extruder1]`, dir inverted |
| X min switch | PC2 | ES1 | `^!PC2` (low = triggered) |
| Y min switch | PC0 | ES2 | `^!PC0` |
| Z min switch (bed up) | PC6 | ES3 | `^!PC6`, Z homing |
| Z max switch (bed down) | PH1 | ES4 | `[rf2000_zmax]` safety + mill homing |

Enable pins are active high. Steps per mm (RF fw): X/Y 4.761875 full steps,
Z 80, E 8, so `rotation_distance` is 42 / 42 / 2.5 / 25 at 32 micro steps.

### DRV8711 drivers (`[drv8711 ...]`, `klippy_extras/drv8711.py`)

| Function | AVR pin | Notes |
|---|---|---|
| SCLK / SDATI (MOSI) / SDATO (MISO) | PD5 / PD6 / PD4 | shared, bit-banged |
| SCS X / Y / Z / E0 / E1 | PL0 / PL2 / PB6 / PL6 / PJ7 | chip select **active high** |
| RESET (all drivers) | PE7 | active high, pulsed at start |
| FAULTn (all drivers) | PE6 | `[gcode_button drv8711_fault]` |
| STALLn X / Y / Z / E0 / E1 | PG0 / PG1 / PG2 / PG3 / PG4 | not used |

The DRV8711 starts with its H-bridges off. Klipper has no DRV8711 driver,
so `drv8711.py` writes the same register values as the Conrad firmware when
Klipper connects (checked bit for bit in `tests/test_units.py`):
CTRL `0x0E29` (32 micro steps), OFF `0x2097`, BLANK `0x31D7`,
DECAY `0x4430`, STALL `0x583C`, DRIVE `0x60F0`, TORQUE `0x11xx`.

### Heaters, sensors, fans, outputs

| Function | AVR pin | Klipper |
|---|---|---|
| Heater T0 / T1 / bed | PB4 / PH6 / PE2 | HZ1 / HZ2 / HZ3 |
| Thermistor T0 / T1 / bed / X35 | PK5 / PK6 / PK7 / PK4 | ADC13 / 14 / 15 / 12, 4.7k pull-up |
| Part fan | PA5 | OUT2, `max_power: 0.757` (RF fw PWM limit 193/255) |
| Case fan (X45) | PF4 | OUT5/FET3, `[controller_fan case_fan]` |
| FET1 X42 / FET2 X44 | PC4 / PC5 | `[output_pin fet1_x42]`, `[output_pin fet2_x44]` |
| 230V output | PE5 | `[output_pin output_230v]` |
| Case light | PA3 | `[led case_light]` |
| RGB red / green / blue | PH3 / PH4 / PH5 | `[led rgb]`, hardware PWM |
| Buzzer | PE3 | `[output_pin beeper]`, `M300` |
| Watchdog WDI | PA7 | left floating (TPS3823 then disables itself), optional `[pwm_tool rf2000_watchdog]` |
| Servo 1 / 2 / 3 | PL3 / PL4 / PL5 | commented out (not fitted on stock printers) |
| I2C SCL / SDA (X34) | PD0 / PD1 | AVR TWI bus `twi` |

### Front panel (`display.cfg`)

| Key / signal | AVR pin | RF fw action | Klipper |
|---|---|---|---|
| LCD RS / E / D4 / D5 / D6 / D7 | PK1 / PK3 / PF5 / PK2 / PA1 / PJ3 | HD44780 4 bit | `[display] lcd_type: hd44780` |
| TAST1 OK | PG5 | OK | menu click |
| TAST2 | PB7 | next | menu down |
| TAST5 | PL7 | previous | menu up |
| TAST4 | PB5 | back | menu back |
| TAST3 | PH0 | right | `RF_KEY_RIGHT` (case light) |
| X12.1 / X12.2 | PJ2 / PJ4 | bed up / down | hold to move Z; babysteps while printing |
| X12.3 / X12.4 | PJ5 / PJ6 | retract / extrude | hold to move filament |
| X12.6 / X12.7 | PH7 / PH2 | continue / pause | `RESUME` / `PAUSE` |

## Geometry

* T0 = left nozzle, T1 = right nozzle, **33.6 mm** to the right of T0
  (`EXT1_X_OFFSET` in the RF firmware).
* The X switch is 33.6 mm left of X=0 (`position_endstop: -33.6`), exactly
  as the RF firmware homes (`minSteps - offX`). Both nozzles can reach X=0,
  and slicer coordinates are the same as with the stock firmware.
* X 0..170 printable with both nozzles (200 for T0 only), Y 0..290, Z 0..185.
* The bed moves in Z. Z=0 is the Z-min switch. Bed mesh values and strain
  gauge probing can go down to Z=-2.

## Parts I could not confirm from sources

These depend on the hardware revision. Check them during first start
([04-first-start.md](04-first-start.md)):

* **Hotend thermistor**: the official RF2000v2 firmware selects sensor type
  3 (EPCOS G550) but labels the V3 hotend as type 13 (NTC 3950); the
  community firmware uses 13. This config uses 13 (V3 hotend). Switch
  `sensor_type` if room temperature reads wrong.
* **ADC chip**: the strain gauge protocol (address 0x49, config byte 0x8C,
  3-byte read) fits the TI ADS1100 and ADS1110. `adc_type` only changes the
  assumed sample rate.
* **DRV8711 readback** (`DRV_DUMP`): SDATO is open-drain. The RF firmware
  reads it slowly with the AVR pull-up enabled. Klipper's software SPI has
  no pull-up, so readback only works if the board has its own pull-up.
  Writing registers, which is all the drivers need, does not depend on it.
