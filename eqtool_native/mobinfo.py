"""Mob info from the Project 1999 wiki (raw wikitext, parsed locally).

Fetches happen on a background thread; results are handed back through a queue
so the UI thread never blocks on the network.
"""
import html
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

WIKI = "https://wiki.project1999.com"
UA = "eqtool-native/1.0 (+Linux; P99 log overlay)"
CACHE_TTL = 7 * 86400
MISS_TTL = 86400

FIELD_ORDER = [
    "zone", "location", "race", "class", "level", "hp", "ac", "agro_radius", "run_speed",
    "attacks_per_round", "attack_speed", "damage_per_hit", "special", "respawn_time",
    "known_loot", "factions", "opposing_factions", "related_quests", "description",
]
HIDDEN = {"imagefilename", "emu_id", "illia_id", "expansion", "image", "name"}
LABELS = {"hp": "HP", "ac": "AC", "hp_regen": "HP regen", "agro_radius": "Aggro radius"}


@dataclass
class MobInfo:
    query: str
    title: str = ""
    url: str = ""
    fields: list = field(default_factory=list)  # [(label, value)]
    respawn_seconds: int = 0
    error: str = ""


# ----------------------------------------------------------- wikitext parsing

def candidate_titles(name):
    n = name.strip().replace("`", "'")
    n = re.sub(r"'s corpse$", "", n)
    bases = [n]
    stripped = re.sub(r"^(a|an|the)\s+", "", n, flags=re.I)
    if stripped != n:
        bases.append(stripped)
    out = []
    for b in bases:
        for t in (b[:1].upper() + b[1:], " ".join(w[:1].upper() + w[1:] for w in b.split())):
            t = t.replace(" ", "_")
            if t and t not in out:
                out.append(t)
    return out


def _split_params(body):
    params, cur, dc, ds, i = [], [], 0, 0, 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ("{{", "}}", "[[", "]]"):
            dc += {"{{": 1, "}}": -1}.get(two, 0)
            ds += {"[[": 1, "]]": -1}.get(two, 0)
            cur.append(two)
            i += 2
            continue
        if body[i] == "|" and dc == 0 and ds == 0:
            params.append("".join(cur))
            cur = []
        else:
            cur.append(body[i])
        i += 1
    params.append("".join(cur))
    return params


def _templates(text):
    """Yield (name, {key: value}) for each top-level {{template}}."""
    i = 0
    while True:
        start = text.find("{{", i)
        if start < 0:
            return
        depth, j = 0, start
        while j < len(text) - 1:
            two = text[j:j + 2]
            if two == "{{":
                depth += 1
                j += 2
            elif two == "}}":
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        body = text[start + 2:j - 2]
        parts = _split_params(body)
        name = parts[0].strip().lower()
        kv = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k.strip().lower()] = v.strip()
        yield name, kv
        i = j


def parse_mob_template(text):
    best = None
    for name, kv in _templates(text):
        if "mob" in name and kv:
            return kv
        if best is None and ("level" in kv or "zone" in kv):
            best = kv
    return best or {}


def clean_wiki(v):
    v = re.sub(r"<!--.*?-->", "", v, flags=re.S)
    v = re.sub(r"<br\s*/?>", "\n", v, flags=re.I)
    for _ in range(3):  # a few passes to unwrap nesting
        v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
        v = re.sub(r"\{\{(?:[^|{}]*\|)*([^|{}]*)\}\}", r"\1", v)
    v = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", v)
    v = re.sub(r"'{2,}", "", v)
    v = re.sub(r"<[^>]+>", "", v)
    v = html.unescape(v)
    lines = []
    for ln in v.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("*"):
            ln = "\u2022 " + ln.lstrip("* ").strip()
        lines.append(ln)
    return "\n".join(lines)


_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """'6 minutes 40 seconds', '6:40', '1h 30m', '22 hours' -> seconds (0 if unknown)."""
    if not text:
        return 0
    t = text.lower()
    total = 0.0
    for num, unit in re.findall(
            r"(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", t):
        total += float(num) * _UNITS[unit[0]]
    if total:
        return int(total)
    m = re.search(r"\b(\d+):(\d{2})(?::(\d{2}))?\b", t)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
    return 0


def build_info(query, title, kv):
    info = MobInfo(query=query, title=title.replace("_", " "),
                   url=f"{WIKI}/{urllib.parse.quote(title)}")
    keys = [k for k in FIELD_ORDER if k in kv] + sorted(
        k for k in kv if k not in FIELD_ORDER and k not in HIDDEN)
    for k in keys:
        val = clean_wiki(kv[k])
        if val:
            info.fields.append((LABELS.get(k, k.replace("_", " ").capitalize()), val))
    info.respawn_seconds = parse_duration(clean_wiki(kv.get("respawn_time", "")))
    return info


# ------------------------------------------------------------------ network

def _get_raw(title, timeout=10):
    url = f"{WIKI}/index.php?title={urllib.parse.quote(title)}&action=raw"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch_mob(query):
    last_err = ""
    for title in candidate_titles(query):
        try:
            text = _get_raw(title)
            hops = 0
            while text and hops < 3:
                m = re.match(r"\s*#REDIRECT\s*\[\[([^\]|#]+)", text, re.I)
                if not m:
                    break
                title = m.group(1).strip().replace(" ", "_")
                text = _get_raw(title)
                hops += 1
        except Exception as e:  # network down, DNS, timeout...
            last_err = f"{type(e).__name__}: {e}"
            break
        if text:
            kv = parse_mob_template(text)
            if kv:
                return build_info(query, title, kv)
    info = MobInfo(query=query, error=last_err or "No wiki page found")
    info.url = f"{WIKI}/index.php?search={urllib.parse.quote(query)}"
    return info


class MobInfoService:
    def __init__(self, cache_path=None, fetcher=fetch_mob):
        self.cache_path = cache_path
        self.fetcher = fetcher
        self.results = queue.Queue()
        self._lock = threading.Lock()
        self._inflight = set()
        self._cache = {}
        self._load()

    def _load(self):
        if not self.cache_path:
            return
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                self._cache = json.load(f)
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("mob cache unreadable; ignoring")
            self._cache = {}

    def _save(self):
        if not self.cache_path:
            return
        try:
            with self._lock:
                data = json.dumps(self._cache)
            tmp = str(self.cache_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.cache_path)
        except Exception:
            log.exception("could not save mob cache")

    def cached(self, name):
        with self._lock:
            ent = self._cache.get(name.lower())
        if not ent:
            return None
        info = MobInfo(**ent["info"])
        info.fields = [tuple(x) for x in info.fields]
        ttl = MISS_TTL if info.error else CACHE_TTL
        return info if time.time() - ent.get("t", 0) < ttl else None

    def request(self, name, force=False):
        name = name.strip()
        if not name:
            return
        if not force:
            hit = self.cached(name)
            if hit:
                self.results.put(hit)
                return
        key = name.lower()
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)
        threading.Thread(target=self._work, args=(name,), daemon=True).start()

    def _work(self, name):
        try:
            info = self.fetcher(name)
        except Exception as e:
            log.exception("mob lookup failed")
            info = MobInfo(query=name, error=str(e))
        with self._lock:
            self._inflight.discard(name.lower())
            # Don't cache network failures, only real answers / real misses.
            if not info.error or info.error == "No wiki page found":
                self._cache[name.lower()] = {"t": time.time(), "info": asdict(info)}
        self._save()
        self.results.put(info)

    def poll(self):
        out = []
        while True:
            try:
                out.append(self.results.get_nowait())
            except queue.Empty:
                return out
