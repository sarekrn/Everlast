"""Zone name -> map file lookup, and a loader for EQ map files (Brewall/Good format).

Map files live in <EverQuest>/maps and look like:
    L x1, y1, z1, x2, y2, z2, r, g, b
    P x, y, z, r, g, b, size, Label_Text
"""
import difflib
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# "You have entered <long name>." -> map short name. Classic, Kunark and Velious.
ZONES = {
    # Antonica
    "Qeynos Hills": "qeytoqrg", "South Qeynos": "qeynos", "North Qeynos": "qeynos2",
    "Qeynos Aqueduct System": "qcat", "Qeynos Catacombs": "qcat",
    "Western Plains of Karana": "qey2hh1", "West Karana": "qey2hh1",
    "Northern Plains of Karana": "northkarana", "North Karana": "northkarana",
    "Eastern Plains of Karana": "eastkarana", "East Karana": "eastkarana",
    "Southern Plains of Karana": "southkarana", "South Karana": "southkarana",
    "Surefall Glade": "qrg", "Blackburrow": "blackburrow", "Everfrost Peaks": "everfrost",
    "Halas": "halas", "Permafrost Caverns": "permafrost", "Permafrost Keep": "permafrost",
    "Befallen": "befallen", "Misty Thicket": "misty", "Rivervale": "rivervale",
    "Runnyeye Citadel": "runnyeye", "Clan Runnyeye": "runnyeye", "Kithicor Forest": "kithicor",
    "West Commonlands": "commons", "East Commonlands": "ecommons",
    "North Freeport": "freportn", "West Freeport": "freportw", "East Freeport": "freporte",
    "Nektulos Forest": "nektulos", "Neriak Foreign Quarter": "neriaka",
    "Neriak - Foreign Quarter": "neriaka", "Neriak Commons": "neriakb",
    "Neriak - Commons": "neriakb", "Neriak Third Gate": "neriakc", "Neriak - 3rd Gate": "neriakc",
    "Lavastorm Mountains": "lavastorm", "Nagafen's Lair": "soldungb", "Solusek's Eye": "soldunga",
    "Temple of Solusek Ro": "soltemple", "Najena": "najena", "The Arena": "arena",
    "Northern Desert of Ro": "nro", "North Ro": "nro", "Southern Desert of Ro": "sro",
    "South Ro": "sro", "Oasis of Marr": "oasis", "Innothule Swamp": "innothule",
    "Grobb": "grobb", "Guk": "guktop", "Upper Guk": "guktop", "Ruins of Old Guk": "gukbottom",
    "Lower Guk": "gukbottom", "Feerrott": "feerrott", "Cazic-Thule": "cazicthule",
    "Temple of Cazic-Thule": "cazicthule", "Oggok": "oggok", "Rathe Mountains": "rathemtn",
    "Lake Rathetear": "lakerathe", "Highpass Hold": "highpass", "High Keep": "highkeep",
    "Estate of Unrest": "unrest", "Plane of Hate": "hateplane", "Plane of Fear": "fearplane",
    "Plane of Sky": "airplane", "Gorge of King Xorbb": "beholder", "Beholder's Maze": "beholder",
    "Splitpaw Lair": "paw", "Infected Paw": "paw", "Kerra Isle": "kerraridge",
    "Kerra Ridge": "kerraridge", "Qeynos Aqueducts": "qcat",
    # Odus
    "Toxxulia Forest": "tox", "Erudin": "erudnext", "Erudin Palace": "erudnint",
    "Paineel": "paineel", "Warrens": "warrens", "Erud's Crossing": "erudsxing",
    "Hole": "hole", "Ruins of Paineel": "hole", "Stonebrunt Mountains": "stonebrunt",
    # Faydwer
    "Greater Faydark": "gfaydark", "Lesser Faydark": "lfaydark",
    "Northern Felwithe": "felwithea", "North Felwithe": "felwithea",
    "Southern Felwithe": "felwitheb", "South Felwithe": "felwitheb",
    "Crushbone": "crushbone", "Clan Crushbone": "crushbone", "Castle Mistmoore": "mistmoore",
    "Butcherblock Mountains": "butcher", "North Kaladim": "kaladimb", "South Kaladim": "kaladima",
    "Kaladim": "kaladima", "Dagnor's Cauldron": "cauldron", "Steamfont Mountains": "steamfont",
    "Ak'Anon": "akanon", "Kedge Keep": "kedge", "Ocean of Tears": "oot",
    # Kunark
    "Field of Bone": "fieldofbone", "Lake of Ill Omen": "lakeofillomen", "Firiona Vie": "firiona",
    "Frontier Mountains": "frontiermtns", "Dreadlands": "dreadlands", "Burning Woods": "burningwood",
    "Skyfire Mountains": "skyfire", "Overthere": "overthere", "Emerald Jungle": "emeraldjungle",
    "Trakanon's Teeth": "trakanon", "Swamp of No Hope": "swampofnohope",
    "Warsliks Woods": "warslikswood", "Cabilis East": "cabeast", "East Cabilis": "cabeast",
    "Cabilis West": "cabwest", "West Cabilis": "cabwest", "Kurn's Tower": "kurn",
    "Kaesora": "kaesora", "Old Sebilis": "sebilis", "Ruins of Sebilis": "sebilis",
    "Chardok": "chardok", "City of Mist": "citymist", "Karnor's Castle": "karnor",
    "Temple of Droga": "droga", "Mines of Nurga": "nurga", "Dalnir": "dalnir",
    "Crypt of Dalnir": "dalnir", "Howling Stones": "charasis", "Charasis": "charasis",
    "Veeshan's Peak": "veeshan", "Timorous Deep": "timorous", "Lost Temple of Cazic-Thule": "cazicthule",
    # Velious
    "Iceclad Ocean": "iceclad", "Thurgadin": "thurgadina", "City of Thurgadin": "thurgadina",
    "Icewell Keep": "thurgadinb", "Great Divide": "greatdivide", "Eastern Wastes": "eastwastes",
    "Cobalt Scar": "cobaltscar", "Crystal Caverns": "crystal", "Tower of Frozen Shadow": "frozenshadow",
    "Kael Drakkal": "kael", "Wakening Land": "wakening", "Western Wastes": "westwastes",
    "Skyshrine": "skyshrine", "Dragon Necropolis": "necropolis", "Temple of Veeshan": "templeveeshan",
    "Siren's Grotto": "sirens", "Sirens' Grotto": "sirens", "Velketor's Labyrinth": "velketor",
    "Sleeper's Tomb": "sleeper", "Plane of Growth": "growthplane", "Plane of Mischief": "mischiefplane",
}


def normalize(name):
    n = name.strip().lower()
    n = re.sub(r"^the\s+", "", n)
    return re.sub(r"[^a-z0-9]", "", n)


_INDEX = {normalize(k): v for k, v in ZONES.items()}


def short_name(long_name, aliases=None, available=None):
    """Map a zone's long name to a map file name. Exact table match first, then
    the map files themselves, then forgiving matches for naming differences."""
    key = normalize(long_name)
    if not key:
        return None
    for k, v in (aliases or {}).items():
        if normalize(k) == key:
            return v
    if key in _INDEX:
        return _INDEX[key]
    avail = set(available or [])
    if key in avail:
        return key
    # e.g. "The City of Kael Drakkal" contains "kaeldrakkal"
    contained = [k for k in _INDEX if len(k) >= 5 and (k in key or key in k)]
    if contained:
        return _INDEX[max(contained, key=len)]
    close = difflib.get_close_matches(key, list(_INDEX), n=1, cutoff=0.82)
    if close:
        return _INDEX[close[0]]
    close = difflib.get_close_matches(key, sorted(avail), n=1, cutoff=0.82)
    return close[0] if close else None


BUNDLED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maps_data")


def user_maps_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "eqtool-native", "maps")


def find_map_dirs(eq_dir):
    """Where to look for maps, in priority order:
    <eq>/maps (and subfolders, since some packs unzip into their own folder),
    maps imported with --import-eqtool-maps, then the maps bundled with this app."""
    dirs = _eq_map_dirs(eq_dir)
    for d in (user_maps_dir(), BUNDLED_DIR):
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs


def _eq_map_dirs(eq_dir):
    if not eq_dir or not os.path.isdir(eq_dir):
        return []
    try:
        base = next((os.path.join(eq_dir, n) for n in os.listdir(eq_dir)
                     if n.lower() == "maps" and os.path.isdir(os.path.join(eq_dir, n))), None)
    except OSError:
        return []
    if not base:
        return []
    dirs = [base]
    try:
        for n in sorted(os.listdir(base)):
            d = os.path.join(base, n)
            if os.path.isdir(d) and any(f.lower().endswith(".txt") for f in os.listdir(d)):
                dirs.append(d)
    except OSError:
        pass
    return dirs


def _as_list(dirs):
    if not dirs:
        return []
    return [dirs] if isinstance(dirs, str) else list(dirs)


def available_maps(map_dirs):
    out = set()
    for d in _as_list(map_dirs):
        try:
            for n in os.listdir(d):
                m = re.match(r"^([a-z0-9]+?)(?:_\d)?\.txt$", n, re.I)
                if m:
                    out.add(m.group(1).lower())
        except OSError:
            pass
    return sorted(out)


@dataclass
class MapData:
    short: str
    lines: list = field(default_factory=list)   # (x1, y1, x2, y2, (r, g, b), z1, z2)
    points: list = field(default_factory=list)  # (x, y, (r, g, b), label, z)

    @property
    def bounds(self):
        xs, ys = [], []
        for x1, y1, x2, y2, *_rest in self.lines:
            xs += (x1, x2)
            ys += (y1, y2)
        for x, y, *_rest in self.points:
            xs.append(x)
            ys.append(y)
        if not xs:
            return (-100.0, -100.0, 100.0, 100.0)
        return (min(xs), min(ys), max(xs), max(ys))


def _floats(parts):
    return [float(p) for p in parts]


def load_map(map_dirs, short):
    """Load <short>.txt and its _1/_2/_3 layers from the first folder that has it."""
    if not short:
        return None
    for d in _as_list(map_dirs):
        md = _load_from(d, short)
        if md:
            return md
    return None


def _load_from(map_dir, short):
    if not os.path.isdir(map_dir):
        return None
    try:
        files = {n.lower(): n for n in os.listdir(map_dir)}
    except OSError:
        return None
    if f"{short}.txt".lower() not in files and f"{short}_1.txt".lower() not in files:
        return None
    md = MapData(short)
    for nm in [f"{short}.txt"] + [f"{short}_{i}.txt" for i in (1, 2, 3)]:
        real = files.get(nm.lower())
        if not real:
            continue
        try:
            with open(os.path.join(map_dir, real), encoding="latin-1") as f:
                for row in f:
                    s = row.strip()
                    if len(s) < 2:
                        continue
                    kind, rest = s[0].upper(), s[1:]
                    parts = [p.strip() for p in rest.split(",")]
                    try:
                        if kind == "L" and len(parts) >= 9:
                            x1, y1, z1, x2, y2, z2, r, g, b = _floats(parts[:9])
                            md.lines.append((x1, y1, x2, y2, (int(r), int(g), int(b)), z1, z2))
                        elif kind == "P" and len(parts) >= 8:
                            x, y, z, r, g, b, _size = _floats(parts[:7])
                            label = ",".join(parts[7:]).replace("_", " ").strip()
                            md.points.append((x, y, (int(r), int(g), int(b)), label, z))
                    except ValueError:
                        continue
        except OSError:
            log.exception("could not read map %s", real)
    return md if (md.lines or md.points) else None


# ------------------------------------------------------------------ search

# Common player shorthand, so "nk", "lguk" or "pof" find the right map.
NICKNAMES = {
    "ecommons": ["EC"], "commons": ["WC"], "qey2hh1": ["WK"], "northkarana": ["NK"],
    "southkarana": ["SK"], "eastkarana": ["EK"], "oot": ["OOT"], "lakeofillomen": ["LOIO"],
    "firiona": ["FV"], "emeraldjungle": ["EJ"], "citymist": ["CoM"], "karnor": ["KC"],
    "charasis": ["HS"], "timorous": ["TD"], "westwastes": ["WW"], "eastwastes": ["EW"],
    "greatdivide": ["GD"], "cobaltscar": ["CS"], "templeveeshan": ["ToV", "NToV"],
    "sebilis": ["Seb"], "gukbottom": ["LGuk"], "guktop": ["UGuk"], "soldungb": ["Solb", "Naggy"],
    "soldunga": ["Sola"], "blackburrow": ["BB"], "highpass": ["HH"], "highkeep": ["HK"],
    "fearplane": ["PoF"], "hateplane": ["PoH"], "airplane": ["PoS"], "growthplane": ["PoG"],
    "mischiefplane": ["PoM"], "skyshrine": ["SS"], "necropolis": ["DN"], "veeshan": ["VP"],
    "sleeper": ["ST"], "velketor": ["Velk"], "wakening": ["WL", "WLotS"], "cazicthule": ["CT"],
    "dreadlands": ["DL"], "burningwood": ["BW"], "skyfire": ["Skyfire"], "overthere": ["OT"],
    "frontiermtns": ["FM"], "trakanon": ["TT"], "swampofnohope": ["SoNH"], "fieldofbone": ["FoB"],
    "crushbone": ["CB"], "gfaydark": ["GFay"], "lfaydark": ["LFay"], "butcher": ["BBM"],
    "permafrost": ["Perma"], "kithicor": ["Kith"], "freportn": ["NFP"], "freportw": ["WFP"],
    "freporte": ["EFP"], "qeynos": ["SQ"], "qeynos2": ["NQ"], "qeytoqrg": ["QH"],
    "lakerathe": ["LR"], "innothule": ["Inny"], "nro": ["NRo"], "sro": ["SRo"],
    "kaesora": ["Kae"], "crystal": ["CC"], "frozenshadow": ["ToFS"], "kael": ["KD"],
    "thurgadina": ["Thurg"], "sirens": ["SG"], "mistmoore": ["MM"], "hole": ["Hole"],
}


def display_name(short):
    """Friendly name for a map file: the first long name in the zone table."""
    for long_name, s in ZONES.items():
        if s == short:
            return long_name
    return short


def zone_choices(available):
    """[(label, short)] for the dropdown, sorted by label."""
    out = [(f"{display_name(s)} ({s})" if display_name(s) != s else s, s) for s in available]
    return sorted(out, key=lambda x: x[0].lower())


def search_zones(query, available, limit=10):
    """Rank available maps against what the user typed. Matches long names
    (all of them, e.g. 'Lower Guk' and 'Ruins of Old Guk'), file names and
    nicknames; word order doesn't matter and small typos are tolerated."""
    q = query.strip().lower()
    if not q:
        return []
    tokens = q.split()
    qn = normalize(q)
    names = {}
    for long_name, s in ZONES.items():
        names.setdefault(s, []).append(long_name.lower())
    scored = []
    for s in available:
        longs = names.get(s, [])
        nicks = [n.lower() for n in NICKNAMES.get(s, [])]
        hay = " ".join(longs + [s] + nicks)
        if q == s or q in nicks or any(normalize(n) == qn for n in longs):
            rank = 0
        elif any(n.startswith(q) or normalize(n).startswith(qn) for n in longs + [s]) or \
                any(n.startswith(q) for n in nicks):
            rank = 1
        elif any(w.startswith(q) for n in longs for w in n.split()):
            rank = 2
        elif all(t in hay for t in tokens) or (len(qn) >= 3 and qn in normalize(hay)):
            rank = 3
        else:
            best = max((difflib.SequenceMatcher(None, qn, normalize(n)).ratio()
                        for n in longs + [s]), default=0)
            if best < 0.72:
                continue
            rank = 4 + (1 - best)
        scored.append((rank, display_name(s).lower(), s))
    scored.sort()
    return [s for _, _, s in scored[:limit]]



# ------------------------------------------------------------------ map levels
# Many zones have several floors drawn on top of each other. EQTool's rule (read
# from its code): the height difference of a line is the smaller of its two end
# points' distance from you in z; of a label, the label's own. Within one "level
# height" it is drawn fully, over the next two level heights it fades, and beyond
# that it is almost gone. Zones EQTool marks "show all levels" skip all of this.
LEVELS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map_levels.json")
_LEVELS = None
MIN_OPACITY = 0.1


def _levels():
    global _LEVELS
    if _LEVELS is None:
        try:
            import json
            with open(LEVELS_FILE, encoding="utf-8") as f:
                _LEVELS = json.load(f)
        except (OSError, ValueError):
            log.exception("map level table unreadable; all levels will be shown")
            _LEVELS = {"default_height": 10.0, "zones": {}}
    return _LEVELS


def level_info(short):
    """(show_all, height) for a zone: EQTool's flag and its level height. A zone
    it does not know is shown whole."""
    data = _levels()
    z = data.get("zones", {}).get((short or "").lower())
    if not z:
        return True, float(data.get("default_height", 10.0))
    return bool(z.get("show_all")), float(z.get("height") or data.get("default_height", 10.0))


def line_dz(z1, z2, player_z):
    """How far (in z) a map line is from you: the nearer of its two ends."""
    return min(abs(z1 - player_z), abs(z2 - player_z))


def level_opacity(dz, height):
    """EQTool's fade: 1.0 within one level height, fading over the next two, then 0.1."""
    if height <= 0 or dz < height:
        return 1.0
    if dz > 3 * height:
        return MIN_OPACITY
    return max(MIN_OPACITY, min(1.0, (3 * height - dz) / (2 * height) + 0.1))


def level_centers(md, height):
    """The floors of a map, as sorted heights. Floors are where a lot of flat
    (same-height) line length sits; ramps and walls spread over many heights and
    don't count. Heights within half a level height of each other are one floor."""
    weights, total = {}, 0.0
    for ax, ay, bx, by, _rgb, z1, z2 in md.lines:
        if abs(z1 - z2) <= 1.0:
            length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
            z = round((z1 + z2) / 2.0)
            weights[z] = weights.get(z, 0.0) + length
            total += length
    if total <= 0:
        return []
    keep = sorted((z, w) for z, w in weights.items() if w >= 0.05 * total)
    clusters = []
    for z, w in keep:
        if clusters and z - clusters[-1][-1][0] <= height / 2.0:
            clusters[-1].append((z, w))
        else:
            clusters.append([(z, w)])
    return [sum(z * w for z, w in c) / sum(w for _, w in c) for c in clusters]


def is_mob_label(rgb):
    """True for the red labels of the P99 maps, which name hostile mobs (places are black,
    vendors and quest NPCs blue, and so on)."""
    r, g, b = rgb
    return r >= 100 and g < 40 and b < 40
