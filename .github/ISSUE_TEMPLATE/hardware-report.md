---
name: Hardware test report
about: Results of the RF2000v2 self-test (docs/09-hardware-validation.md)
title: "Hardware test: "
labels: hardware-test
---

**Printer:** RF2000v2, board revision / year:
**Hotend version (V1/V2/V3):**
**Klipper version (`git -C ~/klipper describe`):**
**klipper-rf2000v2 commit (`git -C ~/klipper-rf2000v2 rev-parse --short HEAD`):**

| Step | Result (OK / FAIL / notes) |
|---|---|
| 1 RF_SELFTEST_DRIVERS | |
| 2 RF_SELFTEST_SENSORS | |
| 3 RF_SELFTEST_ENDSTOPS | |
| 4 RF_SELFTEST_DMS | |
| 5 RF_SELFTEST_OUTPUTS (incl. all 11 keys) | |
| 6 RF_SELFTEST_MOTION | |
| 7 RF_SELFTEST_HEATERS | |
| 8 RF_SELFTEST_PROBE | |
| First print (single / dual) | |

**Console output of the failing steps:**

```
paste here
```

Attach `~/printer_data/logs/klippy.log`.
