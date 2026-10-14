"""Turns log lines into state: spell timers, fights, zone, location, mob lookups.

Every handler is isolated: a bad line or an unexpected message format is logged
and skipped, never allowed to take the app down.
"""
import json
import logging
import os
import re
from collections import deque
from dataclasses import dataclass

from . import maps
from .logwatch import parse_line
from .respawn import RespawnTable
from .events import Boats, ChainTracker, RE_RING_WAR, load_events, parse_ch, ring_war_schedule
from .spells import ROOT_NAMES, TICK, SpellDB, norm_spell
from .trackers import DPSTracker, TimerManager, looks_like_player
from .triggers import TriggerSet

log = logging.getLogger(__name__)

CHARM_BREAK_MSG = "Your charm spell has worn off."
RE_WORN_OFF = re.compile(r"^Your (?P<s>.+?) spell has worn off\.$")


@dataclass
class Alert:
    kind: str   # charm | root | worn | timer_you | timer_other | debuff | custom | spawn | recast
    text: str   # what the alert window shows
    say: str    # what text-to-speech says
    show: bool = True     # put it on the banner
    speak: bool = True    # say it aloud
    key: str = ""         # a new alert with the same key replaces the old one on the banner


RAID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raid_data.json")
RE_NOT_ENRAGED = re.compile(r"^(?P<npc>.+) is no longer enraged\.$")
RE_YOU_RESIST = re.compile(r"^You resist the (.+) spell!$")
RE_SAYS = re.compile(r"^(?P<who>[^,']+?) says, '(?P<what>.*)'$")
ENRAGED_SUFFIX = " has become ENRAGED."
STALE_AFTER = 10.0          # a scheduled alert this long overdue is dropped, not shown late


def load_raid(path=RAID_FILE):
    """Raid data from EQTool: which NPC casts which zone-wide spell, and the Death Touch
    NPCs. Empty if the file is missing."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


RE_BEGIN_CAST = re.compile(r"^You begin casting (.+)\.$")
CANCEL_MSGS = (
    "Your spell is interrupted.", "Your spell fizzles!", "Your spell did not take hold.",
    "You must first select a target for this spell!", "Insufficient Mana to cast this spell!",
    "Your target is out of range, get closer!", "You can't cast spells while stunned!",
    "You cannot see your target.", "Your casting has been interrupted!",
)
FIZZLE_MSG = "Your spell fizzles!"                      # EQTool's own patterns
# Pansy traps (Plane of Mischief): the game gives no known message when you trip one, so
# the alert goes off on the first sign of a pansy, on a system line that talks about a trap,
# or on any phrase you add in Settings (trap_phrases).
RE_PANSY = re.compile(r"\b(?:an angry|a dandy|a pretty ugly|a puny) pansy\b|\bpansies\b", re.I)
TRAP_TEXT = "throws a bunch of pansies at you"   # "a grumpy tree throws a bunch of pansies at you"
RE_TRAP_WORD = re.compile(r"\btrap\b", re.I)
RE_CHAT_LINE = re.compile(r"\b(?:says?|tells?|shouts?|auctions?|says out of character|"
                          r"tell the|told)\b.*'", re.I)
TRAP_COOLDOWN = 60.0
RE_INVITE = re.compile(r"^(?P<who>[A-Za-z][A-Za-z' ]*?) invites you to join (?:a|the) (?P<what>group|raid)\.?$")
INTERRUPT_MSGS = ("Your spell is interrupted.", "Your casting has been interrupted!")
RE_RESIST = re.compile(r"^Your target resisted the (.+) spell\.$")
RE_LOC = re.compile(r"^Your Location is (-?[\d.]+), ?(-?[\d.]+), ?(-?[\d.]+)$")
RE_ZONE = re.compile(r"^You have entered (.+)\.$")
RE_WHO_ZONE = re.compile(r"^There (?:are|is) \d+ players? in (.+)\.$")
RE_LEVEL = [re.compile(r"^You have gained a level! Welcome to level (\d+)!$"),
            re.compile(r"^You have lost a level! You are now level (\d+)[.!]?$")]
RE_WHO = re.compile(r"^\[(\d+) [A-Za-z ]+\] (\w+)\b")
RE_CON = re.compile(
    r"^(?P<mob>.+?) (?:regards you as an ally|looks upon you warmly|kindly considers you|"
    r"judges you amiably|regards you indifferently|looks your way apprehensively|"
    r"glowers at you dubiously|glares at you threateningly|scowls at you, ready to attack)")
# /consider results that mean the mob attacks on sight; every other result is safe.
RE_CON_KOS = re.compile(r"^.+? (?:glares at you threateningly|scowls at you, ready to attack)")
RE_YOU_DIED = re.compile(r"^(?:You have been slain by .+!|You died\.)$")
# EQTool's own pattern: the experience line that follows a kill you shared in.
RE_EXP = re.compile(r"^You gain (?:party |raid )?experience!!")
KILL_PAIR_WINDOW = 6.0     # s between the "slain" line and its experience line
LOC_FRESH = 60.0           # s a /loc stays good enough to label a kill with


@dataclass
class Kill:
    mob: str
    ts: float
    by_you: bool
    where: tuple = None     # /loc at the time, if it was recent
    done: bool = False      # timer already started (a rule), or paired with experience


RE_SLAIN = [re.compile(r"^You have slain (?P<t>.+?)!$"),
            re.compile(r"^(?P<t>.+?) has been slain by (?P<a>.+?)!$"),
            re.compile(r"^(?P<t>.+?) died\.$")]
RE_SIEVE = re.compile(r"^(?P<t>.+?) staggers with mental anguish\.$")
SIEVE_IDLE = 900.0       # a count nobody has added to for this long is dropped (mob died unseen)
RE_DOT = re.compile(r"^(?P<t>.+?) has taken (?P<d>\d+) damage from your (?P<s>.+?)\.$")
RE_NONMELEE = re.compile(r"^(?P<t>.+?) was hit by non-melee for (?P<d>\d+) points? of damage\.$")
RE_YOU_NONMELEE = re.compile(r"^You hit (?P<t>.+?) for (?P<d>\d+) points? of non-melee damage\.$")
RE_MELEE = re.compile(
    r"^(?P<a>.+?) (?:hits?|slash(?:es)?|pierces?|crush(?:es)?|bash(?:es)?|kicks?|punch(?:es)?|"
    r"bites?|claws?|mauls?|gores?|stings?|slices?|smash(?:es)?|strikes?|backstabs?|rends?|"
    r"shoots?|frenz(?:y|ies) on) (?P<t>.+?) for (?P<d>\d+) points? of damage\.$")


CLOCK_STEP = 900.0          # real time zones differ by multiples of 15 minutes
CLOCK_MAX = 15 * 3600.0


def estimate_clock_offset(lines, now):
    """Seconds to ADD to log timestamps so they match this PC's clock.

    EverQuest stamps its log with the clock of the machine it runs on, but
    under Wine that clock can sit in a different time zone (or ignore daylight
    saving) from the rest of your system. A line we have only just read was
    written a moment ago, so any gap bigger than a few seconds is that time
    zone difference; it is snapped to the nearest 15 minutes so small delays
    in writing the log don't matter. Returns None if no line has a timestamp.
    """
    for raw in reversed(lines):
        p = parse_line(raw)
        if p:
            off = round((now - p[0]) / CLOCK_STEP) * CLOCK_STEP
            return float(off) if abs(off) <= CLOCK_MAX else None
    return None


class _Pending:
    __slots__ = ("name", "spells", "ts", "done")

    def __init__(self, name, spells, ts):
        self.name, self.spells, self.ts, self.done = name, spells, ts, False

    @property
    def cast_s(self):
        return max((s.cast_time_ms for s in self.spells), default=0) / 1000.0


class Engine:
    def __init__(self, settings, spells=None, timers=None, dps=None, respawns=None):
        self.settings = settings
        self.spells = spells if spells is not None else SpellDB()
        self.timers = timers if timers is not None else TimerManager()
        self.dps = dps if dps is not None else DPSTracker(settings.num("fight_timeout", 20))
        self.character = None
        self.detected_level = 0
        self.zone = None
        self.loc = None          # (y, x, z) exactly as /loc prints it
        self.trail = deque(maxlen=40)
        self.last_con = None
        self.pending = None
        self.events = deque()    # ("zone", name) / ("loc", loc) / ("con", mob) / ("kill", mob)
        self.respawn_lookup = None  # optional callable(mob) -> seconds (the wiki, if looked up)
        self.respawns = respawns if respawns is not None else RespawnTable()
        self.loc_ts = 0.0
        self.kills = deque(maxlen=30)   # recent deaths, waiting for their experience line
        self.errors = 0
        self.last_ts = 0.0
        self._announced = deque(maxlen=30)
        self._handlers = [
            self._h_worn_off, self._h_cast, self._h_loc, self._h_zone, self._h_level, self._h_con,
            self._h_death, self._h_slain, self._h_exp, self._h_damage, self._h_land_self,
            self._h_fades, self._h_land_other, self._h_invite,
        ]
        # These only look at a line; they never consume it.
        self._handlers[0:0] = [self._h_triggers, self._h_enrage, self._h_death_touch,
                               self._h_raid, self._h_trap, self._h_sieve,
                               self._h_boat, self._h_ringwar, self._h_ch]
        self._trap_last = -1e9
        self.event_data = load_events()
        self.boats = Boats(self.event_data.get("boats", []))
        self.boat_seen = {}           # boat id -> (announcing leg, time of the announcement)
        self.chain = ChainTracker()
        self.sieves = {}              # mob (lower) -> {"name", "n", "first", "last"}
        self.raid = load_raid()
        self.triggers = TriggerSet(settings["triggers"])
        self._trig_src = settings["triggers"]
        self._due = []                # [due, kind, text, say, speak, key]: alerts for later
        self._aoe_last = {}

    # ------------------------------------------------------------ public
    @property
    def level(self):
        ov = int(self.settings.num("level_override", 0))
        return ov if ov > 0 else (self.detected_level or 60)

    def switch_character(self, name):
        """A different character's log is being read: forget the last one's state
        (timers, fights, zone) so nothing of it carries over."""
        self.character = name
        known = (self.settings["character_levels"] or {}).get((name or "").lower(), 0)
        self.detected_level = int(known) if isinstance(known, (int, float)) else 0
        self.zone = None
        self.loc = None
        self.trail.clear()
        self.last_con = None
        self.pending = None
        self.kills.clear()
        self.sieves.clear()
        self.timers.clear()
        self.dps.end_all()
        self._due.clear()
        self.events.append(("character", name))

    def feed_raw(self, line, offset=0.0):
        p = parse_line(line)
        if p:
            self.handle(p[0] + offset, p[1])

    def handle(self, ts, msg):
        self.last_ts = max(self.last_ts, ts)
        for h in self._handlers:
            try:
                if h(ts, msg):
                    return
            except Exception:
                self.errors += 1
                log.exception("handler %s failed on %r", h.__name__, msg)

    def tick(self, now):
        self.last_ts = max(self.last_ts, now)
        try:
            p = self.pending
            if p and not p.done and p.spells and now >= p.ts + p.cast_s + 0.5:
                p.done = True
                s = p.spells[0]
                rec = s.recast_ms / 1000.0
                if self.settings["show_recast"] and rec >= self.settings.num("min_recast", 10):
                    self.timers.add(s.name, "Recast", rec, "recast", p.ts + p.cast_s)
            if p and now > p.ts + p.cast_s + 6:
                self.pending = None
            for t in self.timers.newly_ended(now):
                self._timer_alert(t)
            self._fire_due(now)
            self.timers.purge(now)
            self._boat_timers(now)
            for k in [k for k, r in self.sieves.items() if now - r["last"] > SIEVE_IDLE]:
                del self.sieves[k]
            self.timers.purge(now)
            self.dps.timeout = self.settings.num("fight_timeout", 20)
            self.dps.expire(now)
        except Exception:
            self.errors += 1
            log.exception("tick failed")

    # ------------------------------------------------------------ alerts
    def alert(self, kind, text, say="", show=True, speak=True, key=""):
        self.events.append(("alert", Alert(kind, text, say or text, show, speak, key)))

    def _spell(self, name):
        lst = self.spells.by_name.get((name or "").lower())
        return lst[0] if lst else None

    def _timer_alert(self, t):
        sp = self._spell(t.note)
        if sp and (sp.is_root or sp.is_charm):
            return  # the game's own "worn off" message announces these
        if t.kind in ("buff", "debuff") and t.target != "You" and self._watched(t.note or t.label):
            name = t.note or t.label
            if not self._recently_announced(name, t.target, window=20.0):
                self._watch_fired(name, t.target)
        elif t.kind == "spawn":
            self.alert("spawn", f"Respawn: {t.label}", f"{t.label.split(' @ ')[0]} respawning")
        elif t.kind == "custom":
            self.alert("custom", f"{t.label} done")
        elif t.kind == "recast":
            self.alert("recast", f"{t.label} ready")
        elif t.kind == "boat":
            self.alert("boat", f"{t.target}: {t.label}", f"{t.target}, {t.label}")
        elif t.kind == "ringwar":
            self.alert("ringwar", f"{t.target}: {t.label}",
                       f"{t.target}, {t.label.replace('-- Break --', 'break')}")
        elif t.kind == "ch":
            pass
        elif t.target == "You" and t.kind == "buff":
            # The timer ran out without the game's fade message (or before it).
            name = t.note or t.label
            if not self._recently_announced(name, "you"):
                self._self_buff_faded(name)
        elif t.target == "You":
            self.alert("timer_you", f"{t.note or t.label} ended")
        elif t.mine and t.kind == "buff":
            self._my_buff_faded(t.note or t.label, t.target)
        elif t.kind == "debuff":
            self.alert("debuff", f"{t.note or t.label} ended on {t.target}", f"{t.note} ended")
        else:
            self.alert("timer_other", f"{t.note or t.label} ended on {t.target}",
                       f"{t.note} ended on {t.target}")

    def _watched(self, name):
        """Is this spell on your 'always announce when it wears off' list?"""
        n = norm_spell(name)
        return bool(n) and any(norm_spell(w) == n for w in (self.settings["watch_spells"] or []))

    def _watch_fired(self, name, target=None):
        self._announced.append((name.lower(), (target or "").lower(), self.last_ts))
        where = f" on {target}" if target else ""
        self.alert("watch", f"{name} wore off{where}", f"{name} wore off{where}")

    def _h_worn_off(self, ts, msg):
        if msg == CHARM_BREAK_MSG:
            self.timers.remove_where(lambda t: bool(self._spell(t.note))
                                     and self._spell(t.note).is_charm)
            self.alert("charm", "CHARM BREAK", "Charm break")
            return True
        m = RE_WORN_OFF.match(msg)
        if not m:
            return False
        name = m.group("s")
        sp = self._spell(name)
        # This is the real end of one of our timers for that spell. You may
        # have it on several targets: prefer your own casts, then the one
        # closest to running out.
        cands = [t for t in self.timers.timers.values()
                 if t.note.lower() == name.lower() and t.target != "You"]
        ended = min(cands, key=lambda t: (not t.mine, t.end)) if cands else None
        if ended:
            self.timers.remove(ended.key)
        if (sp and sp.is_root) or name.lower() in ROOT_NAMES:
            self.alert("root", "ROOT BREAK", "Root break")
        elif sp and sp.is_charm:
            self.alert("charm", "CHARM BREAK", "Charm break")
        elif self._watched(name):
            # A spell you asked to hear about (a DoT on a mob, say): always announced.
            if not self._recently_announced(name, ended.target if ended else None, window=20.0):
                self._watch_fired(name, ended.target if ended else None)
        elif ended is not None and ended.kind == "buff":
            if not ended.announced:
                self._my_buff_faded(name, ended.target)
        elif sp and sp.beneficial and ended is None:
            # Your buff on someone we had no timer for (or its timer already
            # ran out and was announced a moment ago).
            if not self._recently_announced(name, None):
                self._my_buff_faded(name, None)
        else:
            self.alert("worn", f"{name} wore off")
        return True

    def _my_buff_faded(self, spell, target):
        self._announced.append((spell.lower(), (target or "").lower(), self.last_ts))
        if target:
            self.alert("my_buff", f"{spell} faded on {target}", f"{spell} faded on {target}")
        else:
            self.alert("my_buff", f"{spell} faded", f"{spell} faded")

    def _recently_announced(self, spell, target, window=60.0):
        for sp, tg, when in self._announced:
            if sp == spell.lower() and (target is None or tg == target.lower()) \
                    and self.last_ts - when <= window:
                return True
        return False

    # ------------------------------------------------------------ helpers
    def _pending_names(self, ts):
        p = self.pending
        if p and ts <= p.ts + p.cast_s + 6:
            return p.name.lower()
        return None

    def _choose(self, cands, ts):
        """Pick the spell a landing message belongs to. Returns
        (spell, ambiguous, cast_by_you). Several spells can share one message
        (Spirit of Wolf / Pack Spirit, Dead Man Floating / Dead Men Floating):
        the one you are casting wins, otherwise the plainest one, i.e. the
        lowest-level spell, which the candidates are already sorted by."""
        mine = self._pending_names(ts)
        if mine:
            for s in cands:
                if s.name.lower() == mine:
                    return s, False, True
        ambiguous = False
        if len({s.name.lower() for s in cands}) > 1:
            # Only worth flagging when the candidates would give different timers.
            ambiguous = len({round(self.spell_seconds(s)) for s in cands}) > 1
        return cands[0], ambiguous, False

    def spell_seconds(self, spell):
        """Duration for this spell: your own correction if you set one, else
        the P99 formula at your level."""
        fixed = (self.settings["spell_durations"] or {}).get(spell.name.lower())
        if fixed:
            return float(fixed)
        return float(spell.duration_seconds(self.level))

    def _extra_tick(self, spell, target):
        """A detrimental spell (a DoT, snare, slow...) on a mob lasts one tick longer than
        its listed duration: EQTool adds 6 s to those, and without it they showed as
        ending a tick early. Your own correction for a spell (right-click) is used as is."""
        if spell.beneficial or target == "You" or looks_like_player(target):
            return False
        return not (self.settings["spell_durations"] or {}).get(spell.name.lower())

    def _add_spell_timer(self, spell, target, ts, ambiguous, mine=False):
        secs = self.spell_seconds(spell)
        if secs <= 0:
            return
        if self._extra_tick(spell, target):
            secs += TICK
        label = spell.name + (" (?)" if ambiguous else "")
        kind = "buff" if spell.beneficial else "debuff"
        key = f"{kind}:{target.lower()}:{spell.name.lower()}"
        self.timers.add(label, target, secs, kind, ts, key=key, note=spell.name, mine=mine)

    # ------------------------------------------------------------ handlers
    def _h_trap(self, ts, msg):
        """Alert when a pansy trap goes off. Returns False always: the line is still
        handled normally (damage, deaths and so on)."""
        if ts - self._trap_last < TRAP_COOLDOWN:
            return False
        hit = None
        low = msg.lower()
        if TRAP_TEXT in low:
            hit = TRAP_TEXT
        for phrase in (self.settings["trap_phrases"] or []):
            if hit is None and phrase and phrase.lower() in low:
                hit = phrase
                break
        if hit is None and RE_PANSY.search(msg):
            hit = "pansy"
        if hit is None and RE_TRAP_WORD.search(msg) and not RE_CHAT_LINE.search(msg):
            hit = "trap"
        if hit:
            self._trap_last = ts
            self.alert("trap", "PANSY TRAP!", "Pansy trap")
        return False

    # ------------------------------------------------------------ later alerts
    def _schedule(self, ts, delay, kind, text, say="", speak=True, key=""):
        self._due.append([ts + delay, kind, text, say, speak, key])

    def _unschedule(self, key):
        self._due = [d for d in self._due if not (key and d[5] == key)]

    def _fire_due(self, now):
        if not self._due:
            return
        keep = []
        for d in sorted(self._due, key=lambda d: d[0]):
            if d[0] > now:
                keep.append(d)
            elif now - d[0] <= STALE_AFTER:
                self.alert(d[1], d[2], d[3], speak=d[4], key=d[5])
        self._due = keep

    # ------------------------------------------------------------ custom triggers
    def _h_triggers(self, ts, msg):
        src = self.settings["triggers"]
        if src is not self._trig_src:           # edited in Settings
            self._trig_src = src
            self.triggers.set(src)
        for _t, shown, spoken in self.triggers.match(msg):
            self.alert("trigger", shown or spoken, spoken or shown, show=bool(shown),
                       speak=bool(spoken))
        return False

    # ------------------------------------------------------------ raid warnings
    # ------------------------------------------------------------ boats, Ring War, CH chain
    BOAT_STALE = 7200.0           # stop renewing a boat's timers this long after its last announcement

    def _h_boat(self, ts, msg):
        """A boat captain's announcement pins down the boat's cycle; the timers follow."""
        if not self.settings["boat_timers"]:
            return False
        leg = self.boats.heard(msg)
        if leg is not None:
            self.boat_seen[leg["boat"]] = (leg, ts)
            self.timers.remove_where(lambda t: t.kind == "boat" and t.key.startswith(f"boat:{leg['boat']}:"))
            self._boat_timers(ts)
        return False

    def _boat_timers(self, now):
        """Make sure every known boat has a timer to its next arrival at each port."""
        if not self.settings["boat_timers"]:
            return
        for boat, (ann, seen) in list(self.boat_seen.items()):
            if now - seen > self.BOAT_STALE:
                del self.boat_seen[boat]
                continue
            for leg in self.boats.siblings(ann):
                key = f"boat:{boat}:{leg['start']}"
                if key in self.timers.timers:
                    continue
                rem = self.boats.remaining(leg, ann, seen, now)
                if rem > 0:
                    self.timers.add(leg["pretty"], leg["name"], rem, "boat", now, key=key,
                                    note=leg["name"])

    def _h_ringwar(self, ts, msg):
        if not self.settings["ringwar_timers"] or not RE_RING_WAR.match(msg):
            return False
        self.timers.remove_where(lambda t: t.kind == "ringwar")
        for wave, label, secs in ring_war_schedule(self.event_data.get("ringwar", {})):
            self.timers.add(label, f"Wave {wave} Ring War", secs, "ringwar", ts,
                            key=f"ringwar:{wave}:{label}", note="Ring War")
        return False

    def _h_ch(self, ts, msg):
        """"001 CH -- Beefwich" in chat: a timer for the cast, and a warning when your
        slot is next."""
        if not self.settings["ch_chain"]:
            return False
        call = parse_ch(msg, ts, self.settings["ch_chain_tag"] or "")
        if call is None:
            return False
        secs = float((self.event_data.get("ch") or {}).get("seconds", 10))
        who = "You" if call.caster == "You" else call.caster
        self.timers.add(f"{call.position} {who}", call.recipient, secs, "ch", ts,
                        key=f"ch:{call.recipient.lower()}:{call.position}", note="CH")
        if self.chain.call(call):
            self.alert("ch_warn", "CH WARNING", say="C H warning")
        return False

    def _h_sieve(self, ts, msg):
        """Mana Sieve landing ("<mob> staggers with mental anguish."): count it, whoever
        cast it. The count goes when the mob dies."""
        m = RE_SIEVE.match(msg)
        if not m:
            return False
        name = m.group("t").strip()
        rec = self.sieves.get(name.lower())
        if rec is None:
            rec = self.sieves[name.lower()] = {"name": name, "n": 0, "first": ts, "last": ts}
        rec["n"] += 1
        rec["last"] = ts
        return False

    def sieve_counts(self, now=None):
        """[(name, count, seconds since the first, seconds since the last)], newest first."""
        now = self.last_ts if now is None else now
        rows = [(r["name"], r["n"], max(0.0, now - r["first"]), max(0.0, now - r["last"]))
                for r in self.sieves.values()]
        return sorted(rows, key=lambda r: r[3])

    def clear_sieves(self, mob=None):
        if mob is None:
            self.sieves.clear()
        else:
            self.sieves.pop(mob.lower(), None)

    def _h_enrage(self, ts, msg):
        """"<mob> has become ENRAGED." Enrage lasts about 12 s."""
        if msg.endswith(ENRAGED_SUFFIX):
            npc = msg[:-len(ENRAGED_SUFFIX)].strip()
            if not npc:
                return False
            key = "enrage:" + npc.lower()
            self._unschedule(key)
            self.alert("enrage", f"{npc} ENRAGED", f"{npc} is enraged", key=key)
            self._schedule(ts, float(self.raid.get("enrage_seconds", 12)), "enrage",
                           f"{npc} ENRAGE OFF", speak=False, key=key)
            return False
        m = RE_NOT_ENRAGED.match(msg)
        if m:
            npc = m.group("npc").strip()
            key = "enrage:" + npc.lower()
            if any(d[5] == key for d in self._due):      # it ended sooner than the timer says
                self._unschedule(key)
                self.alert("enrage", f"{npc} ENRAGE OFF", speak=False, key=key)
        return False

    def _h_death_touch(self, ts, msg):
        """Fright and Dread call out the player they are about to death touch: a single
        word in /say. EQTool shows a 45 s "--DT--" timer for it."""
        m = RE_SAYS.match(msg)
        if not m:
            return False
        npcs = {n.lower() for n in self.raid.get("death_touch_npcs", [])}
        who, what = m.group("who").strip(), m.group("what").strip()
        if who.lower() not in npcs or not what or " " in what:
            return False
        secs = float(self.raid.get("dt_seconds", 45))
        key = f"dt:{what.lower()}"
        self.timers.add(f"--DT-- '{what}'", who, secs, "custom", ts, key=key, note="Death Touch")
        self.timers.timers[key].announced = True          # the warning below is the alert
        if self.character and what.lower() == self.character.lower():
            self.alert("dt", "DEATH TOUCH ON YOU!", "Death touch on you", key="dt")
        else:
            self.alert("dt", f"DEATH TOUCH: {what}", f"Death touch {what}", key="dt")
        return False

    def _h_raid(self, ts, msg):
        """A raid mob's zone-wide spell landed on you or someone else, or you resisted it.
        Looks only; the line is still handled normally."""
        if not self.raid.get("aoe"):
            return False
        cands = self.spells.any_on_you.get(msg)
        if not cands:
            m = RE_YOU_RESIST.match(msg)
            if m:
                cands = self.spells.by_name.get(m.group(1).strip().lower())
        if not cands:
            hit = self.spells.match_on_other(msg, self.spells.any_on_other)
            cands = hit[1] if hit else None
        if cands:
            self._raid_spell(cands, ts)
        return False

    def _aoe_spells(self):
        """Names (as norm_spell) of the zone-wide spells the raid mobs here cast. In a
        zone with no raid data, none; with no zone known yet, every one."""
        table = self.raid.get("aoe") or {}
        zone = self.zone_short()
        zones = [table.get(zone, [])] if zone else list(table.values())
        return {norm_spell(sp): npc["npc"] for npcs in zones for npc in npcs
                for sp in npc.get("spells", [])}

    def _raid_spell(self, cands, ts):
        """A zone-wide raid spell (Dragon Roar, a breath, a cloud) just landed: start its
        timer, and for Dragon Roar the spoken countdown."""
        names = self._aoe_spells()
        for sp in cands or ():
            npc = names.get(norm_spell(sp.name))
            if npc is None:
                continue
            roar = sp.name.lower() == str(self.raid.get("roar_spell", "Dragon Roar")).lower()
            secs = float(self.raid.get("roar_seconds", 36)) if roar else sp.recast_ms / 1000.0
            key = "aoe:" + sp.name.lower()
            if ts - self._aoe_last.get(key, -1e9) < max(10.0, min(secs, 36.0)):
                return                  # everyone in the raid sees the same message
            self._aoe_last[key] = ts
            if secs > 0 and self.settings["raid_aoe_timers"]:
                self.timers.add(sp.name, npc, secs, "custom", ts, key=key, note=sp.name)
                self.timers.timers[key].announced = True
            if roar:
                self._roar(ts, secs)
            return

    def _roar(self, ts, secs):
        """"Dragon Roar out", then a countdown for the next one: "in 6 seconds" six
        seconds before it, and then 4, 2 and 1, as EQTool does."""
        self._unschedule("roar")
        self.alert("roar", "DRAGON ROAR!", "Dragon Roar out", key="roar")
        for left, say in ((6, "Dragon Roar in 6 seconds"), (5, ""), (4, "4 seconds"), (3, ""),
                          (2, "2"), (1, "1")):
            self._schedule(ts, secs - left, "roar", f"Dragon Roar in {left} Seconds!",
                           say=say, speak=bool(say), key="roar")

    def _h_invite(self, ts, msg):
        m = RE_INVITE.match(msg)
        if not m:
            return False
        who, what = m.group("who"), m.group("what")
        self.alert("invite", f"{what.upper()} INVITE: {who}", f"{who} invites you to join a {what}")
        return True

    def _h_cast(self, ts, msg):
        m = RE_BEGIN_CAST.match(msg)
        if m:
            name = m.group(1)
            self.pending = _Pending(name, list(self.spells.by_name.get(name.lower(), [])), ts)
            return True
        if msg == FIZZLE_MSG or msg in INTERRUPT_MSGS:
            name = self.pending.name if self.pending else ""
            self.pending = None
            if msg == FIZZLE_MSG:
                self.alert("fizzle", f"FIZZLE: {name}" if name else "FIZZLE",
                           f"{name} fizzled" if name else "Fizzle")
            else:
                self.alert("interrupt", f"INTERRUPTED: {name}" if name else "INTERRUPTED",
                           f"{name} interrupted" if name else "Interrupted")
            return True
        if msg in CANCEL_MSGS or RE_RESIST.match(msg):
            self.pending = None
            return True
        return False

    def _h_loc(self, ts, msg):
        m = RE_LOC.match(msg)
        if not m:
            return False
        self.loc = tuple(float(g) for g in m.groups())
        self.loc_ts = ts
        self.trail.append(self.loc)
        self.events.append(("loc", self.loc))
        return True

    @staticmethod
    def zone_from(msg):
        """Zone name from a zone-in line or a /who footer, else None."""
        m = RE_ZONE.match(msg)
        if m:
            name = m.group(1).strip()
            if name and not name.lower().startswith(("an area", "a area", "the area")):
                return name
            return None
        m = RE_WHO_ZONE.match(msg)
        if m and m.group(1).strip().lower() != "everquest":
            return m.group(1).strip()
        return None

    def prime(self, lines):
        """Catch up on zone / location / level from log history, without
        creating timers or fights from stale lines."""
        zone, locs = None, []
        for raw in lines:
            p = parse_line(raw)
            if not p:
                continue
            ts, msg = p
            try:
                z = self.zone_from(msg)
                if z:
                    zone, locs = z, []
                    continue
                m = RE_LOC.match(msg)
                if m:
                    locs.append(tuple(float(g) for g in m.groups()))
                    continue
                self._h_level(ts, msg)
            except Exception:
                self.errors += 1
                log.exception("prime failed on %r", msg)
        if zone:
            self.zone = zone
            self.trail.clear()
            self.events.append(("zone", zone))
        if locs:
            for loc in locs[-self.trail.maxlen:]:
                self.trail.append(loc)
            self.loc = locs[-1]
            self.events.append(("loc", self.loc))
        log.info("history: zone=%r locs=%d level=%s", zone, len(locs), self.detected_level)

    def _h_zone(self, ts, msg):
        if not (msg.startswith("You have entered ") or msg.startswith("There ")):
            return False
        name = self.zone_from(msg)
        if not name:
            return msg.startswith("You have entered ")
        if msg.startswith("There ") and self.zone and name.lower() == self.zone.lower():
            return True  # /who confirms the zone we already know
        self.zone = name
        self.loc = None
        self.trail.clear()
        self.timers.remove_where(lambda t: t.kind == "debuff")
        self.dps.end_all()
        self.sieves.clear()
        self.events.append(("zone", name))
        return True

    def _remember_level(self):
        """Keep each character's level, so a switch back does not start at 'assumed 60'."""
        if self.character and self.detected_level:
            lv = dict(self.settings["character_levels"] or {})
            if lv.get(self.character.lower()) != self.detected_level:
                lv[self.character.lower()] = self.detected_level
                self.settings["character_levels"] = lv

    def _h_level(self, ts, msg):
        for rx in RE_LEVEL:
            m = rx.match(msg)
            if m and 1 <= int(m.group(1)) <= 65:
                self.detected_level = int(m.group(1))
                self._remember_level()
                return True
        m = RE_WHO.match(msg)
        if m and self.character and m.group(2).lower() == self.character.lower() \
                and 1 <= int(m.group(1)) <= 65:
            self.detected_level = int(m.group(1))
            self._remember_level()
            return True
        return False

    def _h_con(self, ts, msg):
        m = RE_CON.match(msg)
        if not m:
            return False
        self.last_con = m.group("mob").strip()
        self.events.append(("con", self.last_con))
        kos = bool(RE_CON_KOS.match(msg))
        self.alert("con", "KOS" if kos else "SAFE", say="K O S" if kos else "Safe", key="con")
        return True

    def _h_death(self, ts, msg):
        if not RE_YOU_DIED.match(msg):
            return False
        self.timers.remove_where(lambda t: t.target == "You" and t.kind in ("buff", "debuff"))
        self.dps.end_all()
        return True

    def _h_slain(self, ts, msg):
        for rx in RE_SLAIN:
            m = rx.match(msg)
            if m:
                target = m.group("t").strip()
                self.dps.end_fight(target)
                self.sieves.pop(target.lower(), None)
                self.timers.remove_where(lambda t: t.target.lower() == target.lower()
                                         and t.kind == "debuff")
                self._record_kill(target, ts, by_you=msg.startswith("You have slain"))
                self.events.append(("kill", target))
                return True
        return False

    def _record_kill(self, mob, ts, by_you):
        where = self.loc if (self.loc and ts - self.loc_ts <= LOC_FRESH) else None
        kill = Kill(mob, ts, by_you, where)
        self.kills.append(kill)
        if (self.settings["spawn_rules"] or {}).get(mob.lower()):
            self._start_kill_timer(kill)        # your own rule: no need to wait for experience
        elif not self.settings["spawn_needs_experience"]:
            self._start_kill_timer(kill)

    def _h_exp(self, ts, msg):
        """'You gain experience!!' / 'You gain party experience!!': the kill that
        just happened counted for you, so it gets a respawn timer."""
        if not RE_EXP.match(msg):
            return False
        kill = self._take_kill(ts)
        if kill is not None:
            self._start_kill_timer(kill)
        return True

    def _take_kill(self, ts):
        """The most recent unpaired death within a few seconds before `ts`, a kill
        of yours before one by someone else."""
        while self.kills and ts - self.kills[0].ts > 15:
            self.kills.popleft()
        recent = [k for k in self.kills if not k.done and 0 <= ts - k.ts <= KILL_PAIR_WINDOW]
        if not recent:
            return None
        mine = [k for k in recent if k.by_you]
        return (mine or recent)[-1]

    def spawn_seconds(self, mob):
        """(seconds, source) a mob takes to respawn here: your own rule, else
        EQTool's table for this NPC, else the wiki figure if you looked the mob
        up, else the zone's time, else the default."""
        rule = (self.settings["spawn_rules"] or {}).get(mob.lower())
        if rule:
            return float(rule), "your rule"
        default = self.settings.num("default_spawn_seconds", 400)
        secs, source = self.respawns.lookup(mob, self.zone_short(), default)
        if source in ("npc", "name"):
            return float(secs), source
        if self.respawn_lookup:
            try:
                wiki = self.respawn_lookup(mob)
            except Exception:
                wiki = 0
            if wiki:
                return float(wiki), "wiki"
        return float(secs), source

    def zone_short(self):
        if not self.zone:
            return None
        return maps.short_name(self.zone, self.settings["zone_aliases"])

    def _start_kill_timer(self, kill):
        has_rule = bool((self.settings["spawn_rules"] or {}).get(kill.mob.lower()))
        if kill.done or not (self.settings["auto_spawn_timers"] or has_rule):
            return
        kill.done = True
        secs, _source = self.spawn_seconds(kill.mob)
        self.start_spawn_timer(kill.mob, secs, kill.ts, where=kill.where)

    def start_spawn_timer(self, mob, secs, ts, where="auto"):
        """Start a respawn timer. `where` is the /loc to show (a tuple), None for
        none, or 'auto' for the current /loc if it is recent."""
        if where == "auto":
            where = self.loc if (self.loc and ts - self.loc_ts <= LOC_FRESH) else None
        label = mob + (f" @ {where[0]:.0f}, {where[1]:.0f}" if where else "")
        zone = self.zone or "Unknown zone"
        self.timers.add(label, zone, secs, "spawn", ts,
                        key=f"spawn:{zone.lower()}:{mob.lower()}:{ts:.2f}:{len(self.timers.timers)}")

    def _h_damage(self, ts, msg):
        m = RE_DOT.match(msg)
        if m:
            self.dps.add_damage(ts, "You", m.group("t"), int(m.group("d")))
            return True
        m = RE_YOU_NONMELEE.match(msg)
        if m:
            self.dps.add_damage(ts, "You", m.group("t"), int(m.group("d")))
            return True
        m = RE_NONMELEE.match(msg)
        if m:
            who = "You" if self._pending_names(ts) else "Non-melee"
            self.dps.add_damage(ts, who, m.group("t"), int(m.group("d")))
            return True
        m = RE_MELEE.match(msg)
        if not m:
            return False
        att, tgt, dmg = m.group("a"), m.group("t"), int(m.group("d"))
        if att == "YOU":
            att = "You"
        if tgt in ("YOU", "you", "You"):
            self.dps.add_taken(ts, att, dmg)
        elif looks_like_player(tgt) and not looks_like_player(att) and att != "You":
            self.dps.add_taken(ts, att, dmg)  # a mob hitting a groupmate
        else:
            self.dps.add_damage(ts, att, tgt, dmg)
        return True

    def _h_land_self(self, ts, msg):
        cands = self.spells.on_you.get(msg)
        if not cands:
            return False
        spell, amb, mine = self._choose(cands, ts)
        self._add_spell_timer(spell, "You", ts, amb, mine)
        return True

    def _h_fades(self, ts, msg):
        """The game says a spell on you has faded ("The spirit of wolf leaves you.")."""
        cands = self.spells.fades.get(msg)
        if not cands:
            return False
        names = {s.name.lower() for s in cands}
        gone = self.timers.pop_where(
            lambda t: t.target == "You" and (t.note or t.label.replace(" (?)", "")).lower() in names)
        for sp in cands:
            if self._watched(sp.name) and not self._recently_announced(sp.name, None, window=20.0):
                self._watch_fired(sp.name)
                return True
        buffs = [t for t in gone if t.kind == "buff"]
        for t in buffs:
            name = t.note or t.label.replace(" (?)", "")
            if not self._recently_announced(name, "you", window=20.0):
                self._self_buff_faded(name)
        if not gone:
            # A buff we never saw land (cast before the app started), or whose timer
            # already ran out: the message is still the moment it fell off.
            spell = cands[0]
            if spell.beneficial and not self._recently_announced(spell.name, "you", window=20.0):
                self._self_buff_faded(spell.name)
        return True

    def _self_buff_faded(self, name):
        self._announced.append((name.lower(), "you", self.last_ts))
        self.alert("self_fade", f"{name} faded", f"{name} faded")

    def _h_land_other(self, ts, msg):
        hit = self.spells.match_on_other(msg)
        if not hit:
            return False
        target, cands = hit
        spell, amb, mine = self._choose(cands, ts)
        self._add_spell_timer(spell, target, ts, amb, mine)
        return True
