"""Extract EQTool's raid AOE table (which NPC casts which zone-wide spell) from
EQToolShared.dll into eqtool_native/raid_data.json.

    python3 tools/extract_raid.py EQToolShared.dll

The data is in the static constructor of the ZoneInfo table: for each zone, a list of
NPCThatAOE entries (a name and a SpellEffects list). The Death Touch NPCs (Fright, Dread),
the 45 s Death Touch timer, the 12 s enrage window and Dragon Roar's 30 s + 6 s countdown
come from EQTool.exe's handlers, and are written into the JSON by hand.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dotnet_il as il  # noqa: E402


def main(path):
    M = il.load(path)
    best = None
    for i in range(1, M["rows"][6] + 1):
        r = M["row"](6, i)
        if r[0] and M["st"](r[3]) == ".cctor":
            lines = il.disasm(M, r[0])
            if best is None or len(lines) > len(best):
                best = lines
    ins = []
    for l in best:
        m = re.match(r"\s*\d+: (\S+)\s?(.*)$", l)
        if m:
            ins.append((m.group(1), m.group(2).strip()))
    zone = last_name = prev = None
    expect_zone, buf, out = False, [], {}
    for i, (op, arg) in enumerate(ins):
        if op == "ldsfld" and "ZoneInfoMap" in arg:
            expect_zone = True
        elif op == "ldstr":
            s = arg.strip("'")
            if expect_zone:
                zone, expect_zone = s, False
            prev = s
        elif op == "callvirt" and arg.endswith("set_Name") and prev is not None:
            last_name, buf = prev, []
        elif op == "callvirt" and arg.endswith("MR:Add") and ins[i - 1][0] == "ldstr":
            buf.append(ins[i - 1][1].strip("'"))
        elif op == "stfld" and arg.endswith("SpellEffects"):
            out.setdefault(zone, []).append({"npc": last_name.strip('"'), "spells": list(buf)})
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eqtool_native",
                          "raid_data.json")
    old = json.load(open(target)) if os.path.exists(target) else {}
    old["aoe"] = out
    json.dump(old, open(target, "w"), indent=1)
    print(sum(len(v) for v in out.values()), "AOE NPCs in", len(out), "zones")


if __name__ == "__main__":
    main(sys.argv[1])
