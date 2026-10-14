"""Timer and DPS bookkeeping. Pure data, no UI."""
import re
from collections import deque
from dataclasses import dataclass, field

# ---------------------------------------------------------------- timers

GRACE = {"spawn": 90.0}  # spawn timers linger (shown as UP) after they expire


@dataclass
class Timer:
    key: str
    label: str
    target: str
    start: float
    end: float
    kind: str  # buff | debuff | recast | spawn | custom
    note: str = ""       # spell name for spell timers
    announced: bool = False
    mine: bool = False   # you cast it

    @property
    def duration(self):
        return max(1e-6, self.end - self.start)


def buff_visible(timer, mode):
    """Does the Buff Timers window list this timer? `mode` is the 'Buff timers show'
    setting: 'all', 'mine_and_me' (buffs you cast, plus every buff on you) or 'mine'
    (only buffs you cast). Only buffs are filtered; recasts, custom timers and
    debuffs are not."""
    if timer.kind != "buff":
        return True
    if mode == "mine":
        return timer.mine
    if mode == "mine_and_me":
        return timer.mine or timer.target == "You"
    return True


class TimerManager:
    def __init__(self):
        self.timers = {}
        self.version = 0

    def add(self, label, target, seconds, kind, start, key=None, note="", mine=False):
        key = key or f"{kind}:{target.lower()}:{label.lower()}"
        self.timers[key] = Timer(key, label, target, start, start + seconds, kind, note,
                                 mine=mine)
        self.version += 1
        return key

    def remove(self, key):
        if self.timers.pop(key, None):
            self.version += 1

    def remove_where(self, pred):
        dead = [k for k, t in self.timers.items() if pred(t)]
        for k in dead:
            del self.timers[k]
        if dead:
            self.version += 1
        return len(dead)

    def pop_where(self, pred):
        """Remove the matching timers and hand them back."""
        dead = [t for t in self.timers.values() if pred(t)]
        for t in dead:
            del self.timers[t.key]
        if dead:
            self.version += 1
        return dead

    def clear(self):
        self.timers.clear()
        self.version += 1

    def newly_ended(self, now):
        """Timers that just reached zero (each reported once)."""
        out = []
        for t in self.timers.values():
            if not t.announced and now >= t.end:
                t.announced = True
                out.append(t)
        return out

    def purge(self, now):
        return self.remove_where(lambda t: now > t.end + GRACE.get(t.kind, 0.0))

    def active(self, now):
        self.purge(now)
        return sorted(self.timers.values(), key=lambda t: t.end)


# ---------------------------------------------------------------- DPS

_PLAYERISH = re.compile(r"^[A-Z][a-z]+$")


def looks_like_player(name):
    return bool(_PLAYERISH.match(name)) and name != "You"


@dataclass
class Fight:
    fid: int
    target: str
    start: float
    last: float
    damage: dict = field(default_factory=dict)
    taken: int = 0
    ended: bool = False

    @property
    def total(self):
        return sum(self.damage.values())

    @property
    def duration(self):
        return max(1.0, self.last - self.start + 1)

    def rows(self):
        tot = self.total or 1
        dur = self.duration
        return [(name, dmg, dmg / dur, 100.0 * dmg / tot)
                for name, dmg in sorted(self.damage.items(), key=lambda kv: -kv[1])]

    def summary(self):
        parts = [f"{n} {d} ({dps:.1f})" for n, d, dps, _ in self.rows()[:6]]
        return f"{self.target} in {int(self.duration)}s: " + ", ".join(parts)


class DPSTracker:
    def __init__(self, timeout=20.0):
        self.timeout = timeout
        self.active = {}
        self.history = deque(maxlen=30)
        self.version = 0
        self._next = 1

    def _fight(self, target, ts):
        k = target.lower()
        f = self.active.get(k)
        if f and ts - f.last > self.timeout:
            self.end_fight(target)
            f = None
        if f is None:
            f = Fight(self._next, target, ts, ts)
            self._next += 1
            self.active[k] = f
            self.history.append(f)
        return f

    def add_damage(self, ts, attacker, target, amount):
        f = self._fight(target, ts)
        f.damage[attacker] = f.damage.get(attacker, 0) + amount
        f.last = max(f.last, ts)
        self.version += 1

    def add_taken(self, ts, mob, amount):
        f = self._fight(mob, ts)
        f.taken += amount
        f.last = max(f.last, ts)
        self.version += 1

    def end_fight(self, target):
        f = self.active.pop(target.lower(), None)
        if f:
            f.ended = True
            self.version += 1
        return f

    def end_all(self):
        for k in list(self.active):
            self.end_fight(k)

    def expire(self, now):
        for k, f in list(self.active.items()):
            if now - f.last > self.timeout:
                self.end_fight(k)

    def fights(self):
        """Fights that actually had outgoing damage, oldest first."""
        return [f for f in self.history if f.damage]
