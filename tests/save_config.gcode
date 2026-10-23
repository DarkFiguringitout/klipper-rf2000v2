; Calibration results must be storable with SAVE_CONFIG (options that
; Klipper autosaves must not be defined in the included rf2000v2/ files)
G28
SET_GCODE_OFFSET Z=0.1
Z_OFFSET_APPLY_PROBE
SAVE_CONFIG
