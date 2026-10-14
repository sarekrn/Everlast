"""Plays announcements one after another.

A clip and the computer voice are separate programs, and several alerts can
arrive in the same second (three buffs expiring together, say). Started at once
they would talk over each other. The announcer keeps them in a queue and plays
them in order on a background thread, so the window never waits on audio.

Urgent announcements (a fizzle, an interrupted cast, a charm or root break) are
played before anything normal that is waiting, because what they report needs a
reaction in a second or two. If too many normal ones pile up the oldest are
dropped: an alert that late is not worth hearing.
"""
import collections
import logging
import threading
import time

log = logging.getLogger(__name__)


class Announcer:
    CLIP_REPEAT = 4.0     # s: the same clip is not played again this soon

    def __init__(self, speaker, clips, max_pending=6, start=True):
        self.speaker, self.clips = speaker, clips
        self.max_pending = max_pending
        self.last = ""            # what happened to the most recent voice line
        self.failures = 0
        self._urgent = collections.deque()
        self._normal = collections.deque()
        self._cv = threading.Condition()
        self._closed = False
        self._clip_at = {}
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._loop, name="announcer", daemon=True)
            self._thread.start()

    def announce(self, steps, urgent=False):
        """steps: [("clip", path) | ("say", text), ...], played in order."""
        if not steps:
            return
        with self._cv:
            q = self._urgent if urgent else self._normal
            limit = 4 if urgent else self.max_pending
            while len(q) >= limit:                      # too far behind: drop the oldest
                q.popleft()
            q.append(list(steps))
            self._cv.notify()

    def pending(self):
        with self._cv:
            return len(self._urgent) + len(self._normal)

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def _next(self, block=True):
        with self._cv:
            while True:
                if self._urgent:
                    return self._urgent.popleft()
                if self._normal:
                    return self._normal.popleft()
                if self._closed or not block:
                    return None
                self._cv.wait()

    def _loop(self):
        while True:
            steps = self._next()
            if steps is None:
                return
            self.run(steps)

    def run(self, steps):
        for kind, arg in steps:
            try:
                if kind == "clip":
                    now = time.monotonic()
                    if now - self._clip_at.get(arg, -99) < self.CLIP_REPEAT:
                        continue
                    self._clip_at[arg] = now
                    if not self.clips.play(arg, wait=True):
                        self.last = f"could not play {arg}"
                elif kind == "say":
                    ok, detail = self.speaker.speak(arg)
                    self.last = detail
                    if not ok:
                        self.failures += 1
                        log.warning("voice: %s", detail)
            except Exception:
                self.failures += 1
                log.exception("announcement step %s failed", kind)
