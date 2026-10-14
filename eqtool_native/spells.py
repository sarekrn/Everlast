"""Reads the EverQuest client's spells_us.txt and indexes landing/fade messages."""
import logging
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass

log = logging.getLogger(__name__)

TICK = 6  # seconds per buff tick

# Column layout of spells_us.txt (Titanium-era client, as used by P99).
F_ID, F_NAME = 0, 1
F_ON_YOU, F_ON_OTHER, F_FADES = 6, 7, 8
F_CAST_TIME, F_RECAST = 13, 15
F_DUR_FORMULA, F_DUR = 16, 17
F_GOOD = 83
F_EFFECTS, N_EFFECTS = 86, 12
SPA_CHARM, SPA_ROOT = 22, 99
# EQTool's root list (its spellings kept), plus common variants.
ROOT_NAMES = {"root", "fetter", "enstill", "instill", "immobalize", "immobilize",
              "paralyzing earth", "grasping roots", "ensnaring roots", "enveloping roots",
              "engulfing roots", "engorging roots", "entrapping roots", "bonds of tunare"}
F_CLASSES, N_CLASSES = 104, 14

MAX_TIMER_SECONDS = 6 * 3600  # permanent-ish effects are not worth a timer


def _int(s, default=0):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return default


def duration_ticks(formula, duration, level):
    """Buff duration in ticks. This is the classic table EQTool uses for P99
    (disassembled from EQTool 5.25's SpellDurations.GetDuration_inSeconds);
    formulas 11-15 simply use the listed duration on P99."""
    level = max(1, int(level))
    d = duration
    if formula == 0:
        return 0
    if formula in (1, 6):
        return min(math.ceil(level / 2.0), d)
    if formula == 2:
        return min(math.ceil(level / 5.0 * 3.0), d)
    if formula == 3:
        return min(level * 30, d)
    if formula == 4:
        return d if d else 50
    if formula == 5:
        return d if d else 3
    if formula == 7:
        return min(level, d)
    if formula == 8:
        return min(level + 10, d)
    if formula == 9:
        return min(level * 2 + 10, d)
    if formula == 10:
        return min(level * 3 + 10, d)
    if formula == 50:
        return 72000
    if formula == 3600:
        return d if d else 3600
    return d  # 11-15 and anything unknown


# P99 corrections EQTool applies on top of the client file (ticks).
DURATION_FIXES = {"pacify": 35, "bind sight": 999, "wake of tranquility": 1000, "rampage": 1000}
# Spells EQTool never tracks: teleports, GM/test entries and some target types.
SKIP_NAME_PARTS = ("Translocate", "Translocation", "Prayer to ", " Port", "Journey:",
                   "Ring of ", "Portal", "Alter Plane")
SKIP_NAME_PREFIXES = ("GM ", "Guide ", "NPC")
SKIP_TARGET_TYPES = {0, 15, 20, 32, 36, 37, 40}
F_TARGET = 98


def skip_spell(name, target_type):
    if target_type in SKIP_TARGET_TYPES:
        return True
    if any(p in name for p in SKIP_NAME_PARTS) or name.startswith(SKIP_NAME_PREFIXES):
        return True
    return bool(re.search(r"\b(test|beta)\b", name, re.I))


def norm_spell(name):
    """A spell name reduced for comparing: 'Curse of the Spirits' == 'curse of spirits'."""
    words = re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).split()
    return " ".join(w for w in words if w != "the")


@dataclass
class Spell:
    id: int
    name: str
    cast_on_you: str
    cast_on_other: str
    fades: str
    cast_time_ms: int
    recast_ms: int
    dur_formula: int
    dur_ticks: int
    beneficial: bool
    min_level: int  # lowest level any player class gets it; 0 = NPC only
    target_type: int = -1  # -1 = unknown
    effects: tuple = ()

    @property
    def is_root(self):
        return SPA_ROOT in self.effects or self.name.lower() in ROOT_NAMES

    @property
    def is_charm(self):
        return SPA_CHARM in self.effects

    def caster_level(self, level):
        # EQTool: a caster is at least the level the spell becomes available.
        return max(int(level), self.min_level) if self.min_level else int(level)

    def duration_seconds(self, level):
        return duration_ticks(self.dur_formula, self.dur_ticks, self.caster_level(level)) * TICK

    @property
    def has_duration(self):
        s = self.duration_seconds(60)
        return 0 < s <= MAX_TIMER_SECONDS


class SpellDB:
    def __init__(self):
        self.path = None
        self.spells = {}
        self.by_name = defaultdict(list)
        self.on_you = defaultdict(list)
        self.on_other = defaultdict(list)
        self.fades = defaultdict(list)
        # Every spell with a landing message, instant ones too (a dragon's roar or breath
        # has no duration, so it has no timer, but raid warnings need to see it land).
        self.any_on_you = defaultdict(list)
        self.any_on_other = defaultdict(list)

    def __len__(self):
        return len(self.spells)

    @staticmethod
    def find(eq_dir):
        if not eq_dir or not os.path.isdir(eq_dir):
            return None
        try:
            names = {n.lower(): n for n in os.listdir(eq_dir)}
        except OSError:
            return None
        for cand in ("spells_us.txt", "spells_en.txt"):
            if cand in names:
                return os.path.join(eq_dir, names[cand])
        return None

    def load(self, path):
        spells, by_name = {}, defaultdict(list)
        on_you, on_other, fades = defaultdict(list), defaultdict(list), defaultdict(list)
        any_you, any_other = defaultdict(list), defaultdict(list)
        with open(path, encoding="latin-1") as f:
            for raw in f:
                fld = raw.rstrip("\r\n").split("^")
                if len(fld) <= F_DUR:
                    continue
                sid = _int(fld[F_ID], -1)
                name = fld[F_NAME].strip()
                if sid < 0 or not name:
                    continue
                levels = [_int(x, 255) for x in fld[F_CLASSES:F_CLASSES + N_CLASSES]]
                usable = [lv for lv in levels if 0 < lv < 255]
                s = Spell(
                    id=sid, name=name,
                    cast_on_you=fld[F_ON_YOU].strip(),
                    cast_on_other=fld[F_ON_OTHER].strip(),
                    fades=fld[F_FADES].strip(),
                    cast_time_ms=_int(fld[F_CAST_TIME]),
                    recast_ms=_int(fld[F_RECAST]),
                    dur_formula=_int(fld[F_DUR_FORMULA]),
                    dur_ticks=_int(fld[F_DUR]),
                    beneficial=_int(fld[F_GOOD]) != 0 if len(fld) > F_GOOD else False,
                    min_level=min(usable) if usable else 0,
                    target_type=_int(fld[F_TARGET], -1) if len(fld) > F_TARGET else -1,
                    effects=tuple(_int(x, 254) for x in fld[F_EFFECTS:F_EFFECTS + N_EFFECTS]),
                )
                fix = DURATION_FIXES.get(name.lower())
                if fix:
                    s.dur_ticks = fix
                if name == "Defensive Discipline" and s.cast_on_you.endswith(".."):
                    s.cast_on_you = s.cast_on_you[:-1]
                spells[sid] = s
                by_name[name.lower()].append(s)
                if not skip_spell(name, s.target_type):
                    if s.cast_on_you:
                        any_you[s.cast_on_you].append(s)
                    if s.cast_on_other:
                        any_other[s.cast_on_other].append(s)
                if s.has_duration and not skip_spell(name, s.target_type):
                    if s.cast_on_you:
                        on_you[s.cast_on_you].append(s)
                    if s.cast_on_other:
                        on_other[s.cast_on_other].append(s)
                    if s.fades:
                        fades[s.fades].append(s)
        # For a message several spells share, the likely one comes first:
        # player-castable spells, then the lowest level (Spirit of Wolf before
        # Pack Spirit), then the lowest id.
        for d in (on_you, on_other, fades):
            for lst in d.values():
                lst.sort(key=lambda sp: (sp.min_level == 0, sp.min_level, sp.id))
        self.path = path
        self.spells, self.by_name = spells, by_name
        self.on_you, self.on_other, self.fades = on_you, on_other, fades
        self.any_on_you, self.any_on_other = any_you, any_other
        log.info("loaded %d spells from %s", len(spells), path)
        return self

    def match_on_other(self, msg, table=None):
        """'a gnoll pup adheres to the ground.' -> ('a gnoll pup', [spells]) or None.
        `table` is the message index to use (default: spells that have a duration)."""
        table = self.on_other if table is None else table
        if not table:
            return None
        i = msg.find(" ")
        while i > 0:
            hit = table.get(msg[i + 1:])
            if hit:
                return msg[:i], hit
            i = msg.find(" ", i + 1)
        j = msg.find("'s ")
        while j > 0:  # possessive forms: "Bob's skin turns hard as steel."
            hit = table.get(msg[j:])
            if hit:
                return msg[:j], hit
            j = msg.find("'s ", j + 1)
        return None
