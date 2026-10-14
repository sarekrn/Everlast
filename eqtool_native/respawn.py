"""Respawn times for mobs, by zone.

The data is EQTool's own table (extracted from its EQToolShared.dll by
tools/extract_respawns.py into respawn_data.json): a respawn time for each of
the 121 classic / Kunark / Velious zones, plus per-NPC overrides for some zones.
The lookup order is EQTool's: an exact NPC name, then an NPC name that contains a
listed name (e.g. every "Guard ..." in Butcherblock), then the zone's own time,
and a default for a zone it does not know.
"""
import json
import logging
import os

log = logging.getLogger(__name__)

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "respawn_data.json")
DEFAULT_SECONDS = 400   # 6:40, EQTool's time for a zone it has no entry for


class RespawnTable:
    def __init__(self, path=None):
        self.zones = {}
        self.default = DEFAULT_SECONDS
        self.source = ""
        try:
            with open(path or DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self.zones = data.get("zones", {})
            self.default = int(data.get("default", DEFAULT_SECONDS))
            self.source = data.get("source", "")
        except (OSError, ValueError):
            log.exception("respawn table unreadable; every kill will use the default time")

    def __len__(self):
        return len(self.zones)

    def has_zone(self, zone_short):
        return (zone_short or "").lower() in self.zones

    def lookup(self, npc, zone_short, default=None):
        """(seconds, source) for this NPC in this zone. source is one of
        'npc' (listed by exact name), 'name' (matches a listed name), 'zone'
        (the zone's own time) or 'default' (unknown zone, or a zone with no time)."""
        fallback = int(default or self.default)
        z = self.zones.get((zone_short or "").lower())
        if not z:
            return fallback, "default"
        name = (npc or "").strip().lower()
        if name:
            hit = z.get("exact", {}).get(name)
            if hit:
                return int(hit), "npc"
            for part, secs in z.get("contains", []):
                if part.lower() in name:
                    return int(secs), "name"
        secs = z.get("zone")
        if secs and secs > 0:
            return int(secs), "zone"
        return fallback, "default"
