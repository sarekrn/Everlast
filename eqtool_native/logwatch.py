"""Parsing EverQuest log lines and tailing the active eqlog_*.txt file."""
import logging
import os
import re
import time
from datetime import datetime

log = logging.getLogger(__name__)

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_TS = re.compile(r"^\[\w{3} (\w{3}) +(\d{1,2}) (\d{1,2}):(\d{2}):(\d{2}) (\d{4})\] ?(.*)$")


def parse_line(line):
    """'[Tue Sep 29 12:34:56 2026] message' -> (epoch_seconds, 'message') or None."""
    m = _TS.match(line.rstrip("\r\n"))
    if not m:
        return None
    month = _MONTHS.get(m.group(1))
    if not month:
        return None
    try:
        ts = datetime(int(m.group(6)), month, int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), int(m.group(5))).timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    return ts, m.group(7)


PERSONAL = re.compile(rb"\] (?:You |Your |Welcome to EverQuest)")
LOG_NAME_RE = re.compile(r"^eqlog_(?P<char>[^_]+)_(?P<server>.+)\.txt$", re.I)


class LogWatcher:
    """Tails the most recently written eqlog file. Handles character switches,
    truncation, file replacement and partial lines without ever raising."""

    CHECK_INTERVAL = 1.0
    MAX_READ = 2 * 1024 * 1024

    def __init__(self, eq_dir="", log_file=""):
        self.eq_dir = eq_dir or ""
        self.log_file = log_file or ""
        self.path = None
        self.character = None
        self.server = None
        self._fh = None
        self._ino = None
        self._pos = 0
        self._buf = b""
        self._last_check = -1e9
        self._history = []
        self.forced = ""             # a log you picked by hand (not saved); "" = follow activity
        self._snap = {}              # path -> (mtime_ns, size) as of the last look
        self.opened_at = time.monotonic()
        self.last_data = None        # monotonic time the followed log last gave us a line

    def configure(self, eq_dir, log_file=""):
        if eq_dir != self.eq_dir or log_file != self.log_file:
            self.eq_dir, self.log_file = eq_dir or "", log_file or ""
            self.close()
            self._last_check = -1e9

    def log_dirs(self):
        out = []
        if self.eq_dir and os.path.isdir(self.eq_dir):
            for sub in ("Logs", "logs", ""):
                d = os.path.normpath(os.path.join(self.eq_dir, sub))
                if os.path.isdir(d) and d not in out:
                    out.append(d)
        return out

    def scan(self):
        """{path: (mtime_ns, size)} for every eqlog_*.txt we can see."""
        out = {}
        for d in self.log_dirs():
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                if not LOG_NAME_RE.match(n):
                    continue
                p = os.path.join(d, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out[p] = (st.st_mtime_ns, st.st_size)
        return out

    def list_logs(self):
        """[(path, character, server, mtime)] newest first, for choosing by hand."""
        rows = []
        for p, (mt, _) in self.scan().items():
            m = LOG_NAME_RE.match(os.path.basename(p))
            rows.append((p, m.group("char").capitalize(), m.group("server"), mt / 1e9))
        return sorted(rows, key=lambda r: -r[3])

    def force(self, path=""):
        """Follow this log whatever else is written ("" = back to following activity)."""
        self.forced = path or ""
        self._last_check = -1e9

    def _tail(self, path, start, size):
        """Bytes appended to `path` since `start` (at most the last 256 KB)."""
        start = max(start, size - 256 * 1024, 0)
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                return fh.read(size - start)
        except OSError:
            return b""

    def _last_personal(self, path, size):
        """Timestamp of the newest line only this character's own client writes
        ("You ...", "Your ...", "Welcome to EverQuest"), or 0."""
        data = self._tail(path, size - 128 * 1024, size)
        last = None
        for m in PERSONAL.finditer(data):
            last = m
        if last is None:
            return 0.0
        ls = data.rfind(b"\n", 0, last.start()) + 1
        le = data.find(b"\n", last.start())
        p = parse_line(data[ls: le if le >= 0 else len(data)].decode("utf-8", "replace"))
        return p[0] if p else 0.0

    def find_newest(self):
        """The log to follow: the character you are playing. Channel chat (guild, auction,
        /ooc) is written into the log of every client that is logged in, so a log growing
        does not mean its character is the one being played. Lines only a character's own
        client writes ("You hit...", "You have entered...", "Welcome to EverQuest!") do. So:
        of the logs that grew since the last look, follow one that got such a line (the most
        recently modified if several); if none did, stay where we are. On the first look,
        the log with the newest such line."""
        if self.forced and os.path.isfile(self.forced):
            return self.forced
        if self.log_file:
            return self.log_file if os.path.isfile(self.log_file) else None
        snap = self.scan()
        prev, self._snap = self._snap, snap
        if not snap:
            return None
        if not prev:
            top = sorted(snap, key=lambda p: -snap[p][0])[:6]
            return max(top, key=lambda p: (self._last_personal(p, snap[p][1]), snap[p][0]))
        active = []
        for p, v in snap.items():
            old = prev.get(p)
            if old == v:
                continue
            start = old[1] if (old and v[1] >= old[1]) else 0
            if PERSONAL.search(self._tail(p, start, v[1])):
                active.append(p)
        if active:
            return max(active, key=lambda p: snap[p][0])
        if self.path in snap:
            return self.path
        return max(snap, key=lambda p: snap[p][0])

    def _open(self, path, from_end=True):
        self.close()
        fh = open(path, "rb")
        if from_end:
            fh.seek(0, os.SEEK_END)
            try:
                self._history = scan_history(fh, fh.tell())
            except OSError:
                log.exception("history scan failed")
                self._history = []
            fh.seek(0, os.SEEK_END)
        self._fh = fh
        self._pos = fh.tell()
        self._ino = os.fstat(fh.fileno()).st_ino
        self._buf = b""
        self.path = path
        self.opened_at = time.monotonic()
        self.last_data = None
        m = LOG_NAME_RE.match(os.path.basename(path))
        self.character = m.group("char").capitalize() if m else None
        self.server = m.group("server") if m else None
        log.info("watching %s", path)

    def take_history(self):
        """Lines from before we started watching: from the last zone-in to the end
        of the file. Used to work out the current zone/location/level on startup."""
        h, self._history = self._history, []
        return h

    def close(self):
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
        self.path = None

    def _periodic_check(self):
        newest = self.find_newest()
        if newest and newest != self.path:
            self._open(newest, from_end=True)
            return
        if self.path:
            try:
                ino = os.stat(self.path).st_ino
            except OSError:
                self.close()  # file vanished; will re-find next time
                return
            if ino != self._ino:  # file was replaced (log rotation / archive)
                self._open(self.path, from_end=False)

    def poll(self, now=None):
        """Return new complete lines since the last poll."""
        now = time.monotonic() if now is None else now
        if self._fh is None or now - self._last_check >= self.CHECK_INTERVAL:
            self._last_check = now
            try:
                self._periodic_check()
            except OSError:
                log.exception("log check failed")
                self.close()
        if not self._fh:
            return []
        try:
            size = os.fstat(self._fh.fileno()).st_size
            if size < self._pos:  # truncated
                self._fh.seek(0)
                self._pos = 0
                self._buf = b""
            if size == self._pos:
                return []
            data = self._fh.read(min(size - self._pos, self.MAX_READ))
        except OSError:
            log.exception("log read failed")
            self.close()
            return []
        self._pos += len(data)
        parts = (self._buf + data).split(b"\n")
        self._buf = parts.pop()
        if len(self._buf) > 65536:
            self._buf = b""
        out = []
        for p in parts:
            s = p.decode("utf-8", errors="replace").rstrip("\r")
            if s.strip():
                out.append(s)
        if out:
            self.last_data = now
        return out

    def quiet_seconds(self, now=None):
        """How long the followed log has been silent (since it was opened if it never spoke)."""
        now = time.monotonic() if now is None else now
        return now - (self.last_data if self.last_data is not None else self.opened_at)


ZONE_MARK = b"] You have entered "
SCAN_LIMIT = 64 * 1024 * 1024   # how far back to look for the last zone-in
CHUNK = 1024 * 1024
HISTORY_CAP = 2 * 1024 * 1024   # max bytes of recent lines to replay for loc/level


def _decode_lines(data):
    return [ln.decode("utf-8", errors="replace").rstrip("\r")
            for ln in data.split(b"\n") if ln.strip()]


def scan_history(fh, end):
    """Find the last real 'You have entered X.' line (scanning backwards in chunks)
    and return it plus the lines after it (capped)."""
    found = None
    pos, carry = end, b""
    while pos > 0 and end - pos < SCAN_LIMIT and found is None:
        start = max(0, pos - CHUNK)
        fh.seek(start)
        data = fh.read(pos - start) + carry
        idx = len(data)
        while True:
            idx = data.rfind(ZONE_MARK, 0, idx)
            if idx < 0:
                break
            ls = data.rfind(b"\n", 0, idx) + 1
            if ls == 0 and start > 0:
                break  # line starts in the previous chunk; it's in the carry next round
            le = data.find(b"\n", idx)
            rest = data[idx + len(ZONE_MARK): le if le >= 0 else len(data)].lower()
            if not rest.startswith((b"an area", b"a area", b"the area")):
                found = start + ls
                break
        nl = data.find(b"\n")
        carry = data[:nl + 1] if 0 <= nl < 4096 else data[:4096]
        pos = start
    if found is None:
        tail_start = max(0, end - HISTORY_CAP)
        fh.seek(tail_start)
        lines = _decode_lines(fh.read(end - tail_start))
        return lines[1:] if tail_start > 0 else lines
    fh.seek(found)
    first = fh.readline()
    after = fh.tell()
    tail_start = max(after, end - HISTORY_CAP)
    fh.seek(tail_start)
    rest = _decode_lines(fh.read(end - tail_start))
    if tail_start > after and rest:
        rest = rest[1:]  # partial line
    return _decode_lines(first) + rest
