"""Extract EQTool's respawn tables (zone defaults and per-NPC overrides) from
EQToolShared.dll into eqtool_native/respawn_data.json.

    python3 tools/extract_respawns.py EQToolShared.dll            (the .dll itself)
    python3 tools/extract_respawns.py EQTool_Linux_x_y.zip        (a downloaded EQTool zip)

The data lives in the static constructor of EQToolShared's ZoneParser: for each
zone a ZoneInfo with a RespawnTime and two lists of NpcSpawnTime (exact names
and "name contains"). EQTool's own lookup order is: exact NPC name, then
contains, then the zone's time, and 6:40 for a zone it doesn't know.
"""
import ast
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dotnet_il as il  # noqa: E402

DEFAULT_LEVEL_HEIGHT = 10.0   # most zones in EQTool's table use 10
DEFAULT_SECONDS = 400   # EQTool: new TimeSpan(0, 6, 40) for a zone it does not know


def static_constructors(M):
    """All parsed .cctor bodies, biggest first."""
    out = []
    for i in range(1, M["rows"][6] + 1):
        r = M["row"](6, i)
        if r[0] and M["st"](r[3]) == ".cctor":
            try:
                out.append(il.disasm(M, r[0]))
            except Exception:
                pass
    return sorted(out, key=len, reverse=True)


def parse(lines):
    ins = []
    for l in lines:
        m = re.match(r"\s*\d+: (\S+)\s?(.*)$", l)
        if m:
            ins.append((m.group(1), m.group(2).strip()))
    zones, cur = {}, None
    buf = item = None
    ints, last_str, last_ts = [], "", None
    pending_zone = expect_ctor = False
    prev = ""

    def num(op, arg):
        if op.startswith("ldc.i4.") and op[7:] in list("012345678"):
            return int(op[7:])
        if op == "ldc.i4.m1":
            return -1
        if op in ("ldc.i4.s", "ldc.i4"):
            return int(arg)
        if op == "ldc.r8":
            return float(arg)
        return None

    for op, arg in ins:
        n = num(op, arg)
        if n is not None:
            ints.append(n)
            prev = op
            continue
        if op == "conv.i8":                    # int -> long: the number stays on the stack
            continue
        if op == "ldsfld" and arg == "F:ZoneInfoMap":
            pending_zone = True
        elif op == "ldstr":
            last_str = ast.literal_eval(arg)
            if pending_zone:
                cur = {"name": last_str, "zone": None, "contains": [], "exact": [],
                       "show_all": None, "height": None}
                zones[last_str] = cur
                pending_zone, expect_ctor, buf, item = False, True, None, None
        elif op == "newobj" and arg == "M:.ctor":
            if expect_ctor:
                expect_ctor = False
            elif cur is not None and buf is not None:
                item = {}
        elif op == "newobj" and arg == "MR:.ctor":
            if prev == "dup":
                buf = []                                   # a new list
            elif len(ints) == 1:
                last_ts = ints[0] / 1e7                    # new TimeSpan(ticks)
            elif len(ints) == 3:
                h, m_, s = ints
                last_ts = h * 3600 + m_ * 60 + s           # new TimeSpan(h, m, s)
            elif len(ints) == 4:
                d, h, m_, s = ints
                last_ts = d * 86400 + h * 3600 + m_ * 60 + s
        elif op == "call" and arg == "MR:FromHours" and ints:
            last_ts = ints[-1] * 3600
        elif op == "callvirt" and arg.startswith("M:set_") and cur is not None:
            prop = arg[6:]
            if prop == "Name":
                if item is not None:
                    item["name"] = last_str
            elif prop == "RespawnTime":
                if last_ts is None:
                    raise SystemExit(f"respawn time of {cur['name']!r} was not understood")
                if item is not None:
                    item["seconds"] = last_ts
                    if buf is not None:
                        buf.append(item)
                    item = None
                else:
                    cur["zone"] = last_ts
            elif prop == "NpcContainsSpawnTimes":
                cur["contains"], buf = buf or [], None
            elif prop == "NpcSpawnTimes":
                cur["exact"], buf = buf or [], None
            elif prop == "NotableNPCs":
                buf = None
            elif prop == "ShowAllMapLevels" and ints:
                cur["show_all"] = bool(ints[-1])
            elif prop == "ZoneLevelHeight" and ints:
                cur["height"] = float(ints[-1])
            if prop == "RespawnTime":
                last_ts = None                             # never carry a value over
        ints = []
        prev = op
    return zones


def main(path, out):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith("eqtoolshared.dll"))
            data = z.read(name)
        tmp = out + ".dll.tmp"
        open(tmp, "wb").write(data)
        path = tmp
    M = il.load(path)
    zones = None
    for body in static_constructors(M):
        z = parse(body)
        if len(z) > 50:
            zones = z
            break
    if not zones:
        sys.exit("could not find the zone table in this file")
    table = {}
    for key, z in zones.items():
        table[key.lower()] = {
            "zone": None if z["zone"] is None else int(round(z["zone"])),
            "contains": [[e["name"], int(round(e["seconds"]))] for e in z["contains"]],
            "exact": {e["name"].lower(): int(round(e["seconds"])) for e in z["exact"]},
        }
    json.dump({"default": DEFAULT_SECONDS, "source": "EQTool EQToolShared.dll (ZoneParser)",
               "zones": table}, open(out, "w"), indent=1, sort_keys=True)
    levels = {k.lower(): {"show_all": bool(z["show_all"]),
                          "height": z["height"] if z["height"] is not None else DEFAULT_LEVEL_HEIGHT}
              for k, z in zones.items()}
    json.dump({"source": "EQTool EQToolShared.dll (ZoneParser)",
               "default_height": DEFAULT_LEVEL_HEIGHT, "zones": levels},
              open(os.path.join(os.path.dirname(out), "map_levels.json"), "w"), indent=1, sort_keys=True)
    if path.endswith(".tmp"):
        os.remove(path)
    return table


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "EQToolShared.dll"
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eqtool_native",
                        "respawn_data.json")
    t = main(src, dest)
    no_zone = sorted(k for k, v in t.items() if not v["zone"])   # None or 0 seconds
    print(f"{len(t)} zones, {sum(len(v['exact']) for v in t.values())} exact NPC entries, "
          f"{sum(len(v['contains']) for v in t.values())} 'name contains' entries")
    print("zones with no zone-wide respawn (the default applies):", no_zone or "none")
