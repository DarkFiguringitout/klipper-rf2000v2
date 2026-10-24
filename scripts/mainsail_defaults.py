#!/usr/bin/env python3
# Mainsail defaults for the RF2000v2: temperature presets and a tidy macro
# panel (the ~60 RF M-code aliases stay usable from G-code, but are hidden
# from the buttons).  Safe to run again; existing presets of the same name
# are replaced.
#
#   python3 scripts/mainsail_defaults.py [moonraker-url]
import json
import re
import sys
import urllib.request
import uuid

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7125"
NS = "mainsail"

# name: (nozzle, bed) - both nozzles are heated, an empty one does no harm
PRESETS = {"PLA": (210, 60), "PETG": (240, 80), "ABS": (250, 100),
           "TPU": (225, 50)}
# Mainsail has its own buttons for these, or they are key/fault handlers
HIDDEN = {"PRINT_START", "PRINT_END", "PAUSE", "RESUME", "CANCEL_PRINT",
          "BED_MESH_PROFILE", "RF_KEY_RIGHT", "RF_KEY_HOLD", "RF_KEY_RELEASE",
          "RF_DRIVER_FAULT"}


def call(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=10))["result"]


def item(key, value):
    call("POST", "/server/database/item",
         {"namespace": NS, "key": key, "value": value})


def main():
    db = call("GET", "/server/database/item?namespace=" + NS)["value"]
    old = db.get("presets", {}).get("presets", {})
    for pid, p in old.items():
        if p.get("name") in PRESETS:
            call("DELETE", "/server/database/item?namespace=%s&key=presets.presets.%s" % (NS, pid))
    for name, (nozzle, bed) in PRESETS.items():
        heat = lambda v: {"bool": True, "type": "heater", "value": v}
        item("presets.presets." + str(uuid.uuid4()),
             {"name": name, "gcode": "",
              "values": {"extruder": heat(nozzle), "extruder1": heat(nozzle),
                         "heater_bed": heat(bed)}})
    item("presets.cooldownGcode", "TURN_OFF_HEATERS")

    objs = call("GET", "/printer/objects/list")["objects"]
    macros = [o.split(" ", 1)[1].upper() for o in objs
              if o.startswith("gcode_macro ")]
    hidden = sorted(m for m in macros
                    if m in HIDDEN or re.fullmatch(r"M\d+(\.\d+)?", m))
    item("macros.hiddenMacros", hidden)
    print("presets: %s; %d of %d macros hidden"
          % (", ".join(PRESETS), len(hidden), len(macros)))


if __name__ == "__main__":
    main()
