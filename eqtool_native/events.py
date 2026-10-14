"""Raid-night helpers taken from EQTool: boat schedules, Ring War rounds and the
Complete Heal chain. Pure logic, no UI (the engine feeds it log lines and shows the
results as timers and alerts).

The numbers come from EQTool itself (boat announcements and trip times, the Ring War
round lengths) and are kept in event_data.json.
"""
import json
import os
import re
from dataclasses import dataclass

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "event_data.json")


def load_events(path=DATA_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ boats
class Boats:
    """Boat legs. Each boat has one or two legs (one per port); a boat announcement in
    the log pins down where the boat is in its cycle, and from then on the time to the
    next arrival at each port follows from the trip time."""

    def __init__(self, legs):
        self.legs = list(legs)

    def heard(self, msg):
        """The leg whose announcement this line is, or None."""
        for leg in self.legs:
            a = leg.get("announce") or ""
            if a and msg.startswith(a):
                return leg
        return None

    def siblings(self, leg):
        """[the announcing leg, then the boat's other leg(s)]."""
        return [leg] + [x for x in self.legs if x["boat"] == leg["boat"] and x is not leg]

    @staticmethod
    def remaining(leg, announced, seen, now):
        """Seconds until the boat next docks at `leg`'s port, given that the boat's
        `announced` leg was announced at `seen`. (EQTool's own arithmetic: the time since
        the announcement is taken modulo the announcing leg's trip time; a dock time already
        passed means the next lap.)"""
        elapsed = abs(now - seen)
        trip = announced["trip"]
        if elapsed > trip:
            elapsed -= int(elapsed / trip) * trip
        rem = leg["dock"] - elapsed
        if rem <= 0:
            rem = leg["trip"] - elapsed + leg["dock"]
        return rem


# ------------------------------------------------------------------ ring war
RE_RING_WAR = re.compile(r"^Seneschal Aldikar shouts,? '?TROOPS, TAKE YOUR POSITIONS!'?$")


def ring_war_schedule(cfg):
    """[(wave, label, seconds from the shout)]: every round, then a break, per wave.
    Rounds come `round_seconds` apart; each wave ends with a `break_seconds` rest."""
    waves = int(cfg.get("waves", 3))
    rounds = int(cfg.get("rounds", 7))
    rs, bs = float(cfg.get("round_seconds", 210)), float(cfg.get("break_seconds", 300))
    out, cum = [], 0.0
    for w in range(1, waves + 1):
        for r in range(1, rounds + 1):
            cum += rs
            out.append((w, f"Round {r}", cum))
        cum += bs
        out.append((w, "-- Break --", cum))
    return out


# ------------------------------------------------------------------ CH chain
RE_CHAT = re.compile(
    r"^(?P<who>You|[A-Za-z][A-Za-z']*) (?:tells? (?:the|your) (?:raid|group|guild|party)|"
    r"says? out of character|says?|shouts?|auctions?),? ?'(?P<text>.*)'$")
RE_CH_WORD = re.compile(r"(?<![A-Za-z0-9])r?ch(?![A-Za-z0-9])", re.I)


@dataclass
class CHCall:
    caster: str
    position: str
    recipient: str
    ts: float


def parse_ch(msg, ts, tag=""):
    """A Complete Heal call in a chat line ("001 CH -- Beefwich", "Aaa CH Tank", "ramp
    CH Cleric"...) as a CHCall, or None. The position is the 3-character (or single
    letter) token in front of "CH"; the target is the word after it."""
    m = RE_CHAT.match(msg)
    if not m:
        return None
    caster, text = m.group("who"), m.group("text").strip()
    if tag:
        if not text.startswith(tag):
            return None
        text = text[len(tag):].strip()
    hit = RE_CH_WORD.search(text)
    if not hit:
        return None
    before = text[:hit.start()].split()
    if len(before) > 2:
        return None
    pos = ""
    for tok in reversed(before):
        tok = tok.strip("-:.,()[]")
        if len(tok) == 3 or (len(tok) == 1 and tok.isalpha()):
            pos = tok
            break
    if not pos:
        for tok in reversed(before):
            if "ramp" in tok.lower() and len(tok) in (4, 5, 6):
                pos = tok
                break
    pos = pos or "000"
    rest = re.sub(r"^[\s\-:.,>=]+", "", text[hit.end():]).strip()
    rest = re.sub(r"[^A-Za-z' ]", " ", rest).split()
    if not rest or len(rest) > 2:
        return None
    rec = next((w for w in rest if len(w) >= 3), "")
    if not rec:
        return None
    return CHCall(caster, pos, rec.capitalize(), ts)


class ChainTracker:
    """Which chain slots have been called for each target, and when to warn you that your
    own slot is next. Your slot is the position of a call you made yourself."""

    IDLE = 30.0       # a chain nobody has called for this long is forgotten

    def __init__(self):
        self.chains = {}

    def _chain(self, call):
        for k in [k for k, c in self.chains.items() if call.ts - c["updated"] > self.IDLE]:
            del self.chains[k]
        c = self.chains.setdefault(call.recipient.lower(),
                                   {"highest": "", "yours": "", "updated": call.ts})
        c["updated"] = call.ts
        return c

    def call(self, call):
        """Record a call; True if you should be warned (the slot before yours was just called)."""
        c = self._chain(call)
        pos = call.position
        mine = call.caster == "You"
        if mine:
            c["yours"] = pos
        new_high = False
        if not c["highest"] or pos.lower() > c["highest"].lower():
            c["highest"] = pos
            new_high = True
        if mine or not c["yours"]:
            return False
        return self._should_warn(c, pos, new_high)

    @staticmethod
    def _should_warn(c, pos, new_high):
        hi, yours = c["highest"], c["yours"]
        if hi[:1].isalpha() and pos[:1].isalpha() and yours[:1].isalpha():
            h, y, p = hi[0].lower(), yours[0].lower(), pos[0].lower()
            diff = ord(y) - ord(p)
            if y == "a" and new_high and diff == 1:
                return False
            if h == "z" and y == "a" and p == "z" and not new_high:
                return True
            return diff == 1
        if hi[:1].isdigit() and pos[:1].isdigit() and yours[:1].isdigit():
            try:
                h, y, p = int(hi), int(yours), int(pos)
            except ValueError:
                return False
            diff = y - p
            if y == 1 and new_high and diff == 1:
                return False
            if p == h and y == 1 and not new_high:
                return True
            return diff == 1
        return False
