; Mill mode: Z homes to the Z-max switch, spindle via 230V output
RF_MODE MODE=MILL
G28
G1 X50 Y50 F3000
M3
G1 Z170 F300
M5
HOME_Z_MAX
RF_MODE MODE=PRINT
G28
