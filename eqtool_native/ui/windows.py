"""The four overlay windows."""
import logging
import re
import tkinter as tk
import webbrowser
from tkinter import simpledialog, ttk

from .. import maps
from ..trackers import buff_visible
from .base import KIND_COLOR, P, OverlayWindow, fmt_secs

log = logging.getLogger(__name__)


def parse_hms(text):
    """'6:40' -> 400, '1:02:03' -> 3723, '7' -> 420 (minutes), '90s' -> 90."""
    t = (text or "").strip().lower()
    if not t:
        return 0
    if re.fullmatch(r"\d+(\.\d+)?s", t):
        return int(float(t[:-1]))
    if ":" in t:
        try:
            nums = [int(x) for x in t.split(":")]
        except ValueError:
            return 0
        total = 0
        for n in nums:
            total = total * 60 + n
        return total
    try:
        return int(float(t) * 60)
    except ValueError:
        return 0


# ====================================================================== timers
def is_spawn_timer(t):
    """A mob you killed, due to respawn: has its own window."""
    return t.kind == "spawn"


def is_mob_timer(t, settings):
    """Your debuffs on mobs, when they are shown apart from your buffs."""
    return t.kind == "debuff" and bool(settings["mob_timers_debuffs"])


class TimersWindow(OverlayWindow):
    """Buffs on you and others, recasts and custom timers."""
    name, title, default_geometry = "timers", "Buff Timers", "280x420+40+120"
    empty_text = "No active buff timers"
    solid = True   # bars and text stay 100% opaque; the opacity slider fades the background

    default_view_height = 300      # px: how tall the list is, unless you have set it

    def wants(self, t):
        if is_spawn_timer(t) or is_mob_timer(t, self.app.settings):
            return False
        return buff_visible(t, self.app.settings["buff_filter"])

    def build(self):
        bar = self.bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=4, pady=(4, 0))
        self._bar_shown = True
        self.build_toolbar(bar)
        self.scroll_y = 0.0
        self._content_h = self._view_h = 0
        self._row_h = 24
        self._applied_h = None         # the window height this code last set
        self.vbar = ttk.Scrollbar(self.win, orient="vertical", command=self._on_vbar)
        self._vbar_shown = False
        # In play mode the strip is the one part of the window that stays clickable, so
        # the list can be scrolled without unlocking. While you work it the window must
        # not hide itself under the mouse (hover-hide), or you could not drag the slider.
        self.vbar.bind("<ButtonPress-1>", lambda e: setattr(self, "hold_ghost", True), add="+")
        self.vbar.bind("<ButtonRelease-1>", lambda e: setattr(self, "hold_ghost", False), add="+")
        self.vbar.bind("<Button-4>", lambda e: self._wheel(e, -1))
        self.vbar.bind("<Button-5>", lambda e: self._wheel(e, +1))
        self.vbar.bind("<MouseWheel>", lambda e: self._wheel(e, -1 if e.delta > 0 else +1))
        self.c = self.canvas(self.win)
        self.c.pack(side="left", fill="both", expand=True, padx=2, pady=4)
        self.c.bind("<Button-3>", self.on_right_click)
        self.c.bind("<Button-4>", lambda e: self._wheel(e, -1))
        self.c.bind("<Button-5>", lambda e: self._wheel(e, +1))
        self.c.bind("<MouseWheel>", lambda e: self._wheel(e, -1 if e.delta > 0 else +1))
        self.rows = []
        self._solid_canvas = []
        self.draggable(bar, self.c)

    # -- the list's height, and scrolling it
    def view_height(self):
        """How tall the list is (the part below the toolbar), as you set it."""
        v = (self.app.settings["timer_view_heights"] or {}).get(self.name)
        try:
            v = float(v) if v else float(self.default_view_height)
        except (TypeError, ValueError):
            v = float(self.default_view_height)
        return int(max(60, min(1200, v)))

    def set_view_height(self, h):
        per = dict(self.app.settings["timer_view_heights"] or {})
        per[self.name] = int(max(60, min(1200, h)))
        self.app.settings["timer_view_heights"] = per

    def interactive_rects(self):
        """In play mode the scrollbar is the only part that takes clicks."""
        if not self._vbar_shown:
            return []
        try:
            return [(self.vbar.winfo_x(), self.vbar.winfo_y(),
                     self.vbar.winfo_width(), self.vbar.winfo_height())]
        except tk.TclError:
            return []

    def ghost_rect(self):
        """The mouse hides the bars, not the scroll strip beside them: reaching the strip
        from outside must not make the window vanish before you get to it."""
        x, y, w, h = super().ghost_rect()
        if self._vbar_shown:
            try:
                w = max(1, w - self.vbar.winfo_width())
            except tk.TclError:
                pass
        return (x, y, w, h)

    def _clamp_scroll(self):
        self.scroll_y = max(0.0, min(self.scroll_y, max(0.0, self._content_h - self._view_h)))

    def _on_vbar(self, *args):
        """The scrollbar was dragged or clicked."""
        try:
            if args[0] == "moveto":
                self.scroll_y = float(args[1]) * self._content_h
            elif args[0] == "scroll":
                n = int(args[1])
                self.scroll_y += n * (self._row_h if args[2] == "units" else self._view_h * 0.9)
        except (ValueError, IndexError):
            return
        self._clamp_scroll()
        self.refresh(self._now())

    def _wheel(self, e, direction):
        if e.state & 0x4:               # Ctrl + wheel is this window's opacity, not scrolling
            return
        before = self.scroll_y
        self.scroll_y += direction * self._row_h * 2
        self._clamp_scroll()
        if self.scroll_y != before:
            self.refresh(self._now())

    @staticmethod
    def _now():
        import time
        return time.time()

    # -- window height: your list height, or less in play mode when the list is short
    def _wanted_height(self):
        """Total window height. Unlocked you see the full list height you chose; in play
        mode (locked) the window is only as tall as the timers, up to that height, so
        it does not sit there as a big empty box (and an empty area is where a
        desktop may show leftover graphics)."""
        view = self.view_height()
        if self.locked and bool(self.app.settings["shrink_locked_overlays"]):
            view = min(view, max(2, int(self._content_h)))
        want = view + 8                                  # canvas padding above and below
        if self._handle_shown:
            want += int(self._handle.winfo_reqheight() or 0)
        if self._bar_shown:
            want += int(self.bar.winfo_reqheight() or 0) + 4
        return max(2, want)

    def _chrome_height(self):
        want = 8
        if self._handle_shown:
            want += int(self._handle.winfo_reqheight() or 0)
        if self._bar_shown:
            want += int(self.bar.winfo_reqheight() or 0) + 4
        return want

    def _fit(self):
        try:
            w, cur = self.win.winfo_width(), self.win.winfo_height()
            if not self.borderless and self._applied_h is not None \
                    and abs(cur - self._applied_h) > 2 and cur > self._chrome_height() + 60:
                # A normal window with a title bar: its edge is the control. The height you
                # dragged it to becomes the list height, rather than being fought.
                self.set_view_height(cur - self._chrome_height())
            want = self._wanted_height()
            self.win.minsize(160, 1)
            if self._applied_h is None or abs(cur - want) > 1:
                self.win.geometry(f"{w}x{want}")
            self._applied_h = want
        except tk.TclError:
            pass

    # the corner grip sets the list's height (and the width) instead of the window's
    def _resize_start(self, e):
        if not self.movable:
            self._resize_from = None
            return "break"
        self._resize_from = (e.x_root, e.y_root, self.win.winfo_width(), self.view_height())
        return "break"

    def _resize_move(self, e):
        if not getattr(self, "_resize_from", None):
            return "break"
        x0, y0, w0, v0 = self._resize_from
        w = max(160, w0 + e.x_root - x0)
        self.set_view_height(v0 + e.y_root - y0)
        try:
            self.win.geometry(f"{w}x{self._wanted_height()}")
        except tk.TclError:
            pass
        return "break"

    def on_chrome(self):
        """In play mode (click-through) the buttons can't be used, so hide the
        toolbar and leave just the timers."""
        show = not self.passthrough
        if self.passthrough:
            self.scroll_y = 0.0          # in play mode you can't scroll, so show the top of the list
        if show and not self._bar_shown:
            self.bar.pack(fill="x", padx=4, pady=(4, 0),
                          before=(self.vbar if self._vbar_shown else self.c))
            self._bar_shown = True
        elif not show and self._bar_shown:
            self.bar.pack_forget()
            self._bar_shown = False

    def solid_rects(self):
        """What must stay visible and opaque: the timer rows and group names,
        the toolbar (when shown) and the resize grip (when shown)."""
        try:
            wx, wy = self.win.winfo_rootx(), self.win.winfo_rooty()
            ox, oy = self.c.winfo_rootx() - wx, self.c.winfo_rooty() - wy
        except tk.TclError:
            return None
        out = []
        for x, y, w, h in self._solid_canvas:          # only what is inside the visible part of the list
            y0, y1 = max(y, 0), min(y + h, self._view_h)
            if y1 > y0:
                out.append((ox + x, oy + y0, w, y1 - y0))
        shown = []
        if self._vbar_shown:
            shown.append(self.vbar)
        if self._handle_shown:
            shown.append(self._handle)
        if self._bar_shown:
            shown.append(self.bar)
        grip = getattr(self, "_grip", None)
        if grip is not None and self.movable:
            shown.append(grip)
        for widget in shown:
            out.append((widget.winfo_rootx() - wx, widget.winfo_rooty() - wy,
                        widget.winfo_width(), widget.winfo_height()))
        return out

    def build_toolbar(self, bar):
        ttk.Button(bar, text="Add timer", style="Tool.TButton", command=self.add_custom).pack(side="left")
        ttk.Button(bar, text="Clear", style="Tool.TButton", command=self.clear).pack(side="left", padx=4)
        ttk.Label(bar, text="right-click for options", style="Dim.TLabel",
                  font=self.app.fonts["small"]).pack(side="right")

    def clear(self):
        """Clear only the timers this window shows."""
        self.app.engine.timers.remove_where(self.wants)

    def add_custom(self):
        label = simpledialog.askstring("Custom timer", "Label:", parent=self.win)
        if not label:
            return
        dur = simpledialog.askstring("Custom timer", "Duration (mm:ss, or minutes):", parent=self.win)
        secs = parse_hms(dur)
        if secs > 0:
            import time
            self.app.engine.timers.add(label, "Custom", secs, "custom", time.time())

    def on_right_click(self, ev):
        y = self.c.canvasy(ev.y)
        t = None
        for y0, y1, key in self.rows:
            if y0 <= y <= y1:
                t = self.app.engine.timers.timers.get(key)
                break
        if not t:
            return
        menu = tk.Menu(self.win, tearoff=0, bg=P["panel"], fg=P["text"],
                       activebackground=P["accent"], activeforeground=P["bg"])
        menu.add_command(label="Remove this timer",
                         command=lambda k=t.key: self.app.engine.timers.remove(k))
        if t.kind in ("spawn", "custom"):
            menu.add_command(label="Restart this timer", command=lambda tm=t: self.restart(tm))
        spell = t.note if t.kind in ("buff", "debuff") else ""
        if spell:
            menu.add_separator()
            menu.add_command(label=f"Set duration for {spell}...",
                             command=lambda tm=t: self.set_spell_duration(tm))
            watched = self.app.engine._watched(spell)
            menu.add_command(
                label=(f"Stop announcing when {spell} wears off" if watched
                       else f"Announce when {spell} wears off"),
                command=lambda sp=spell: self.toggle_watch(sp))
            if (self.app.settings["spell_durations"] or {}).get(spell.lower()):
                menu.add_command(label=f"Use default duration for {spell}",
                                 command=lambda sp=spell: self.clear_spell_duration(sp))
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            menu.grab_release()

    def toggle_watch(self, spell):
        """Add or remove a spell from the 'always announce when it wears off' list."""
        from ..spells import norm_spell
        n = norm_spell(spell)
        cur = list(self.app.settings["watch_spells"] or [])
        if any(norm_spell(w) == n for w in cur):
            cur = [w for w in cur if norm_spell(w) != n]
        else:
            cur.append(spell)
        self.app.settings["watch_spells"] = cur
        self.app.settings.save()

    def restart(self, t):
        import time
        dur = t.end - t.start
        t.start = time.time()
        t.end = t.start + dur
        t.announced = False
        self.app.engine.timers.version += 1

    def set_spell_duration(self, t):
        spell = t.note
        val = simpledialog.askstring(
            "Spell duration",
            f"How long does {spell} last? (mm:ss or h:mm:ss)\n"
            "Used for every future cast; right-click again to go back to the default.",
            initialvalue=fmt_secs(t.end - t.start), parent=self.win)
        secs = parse_hms(val) if val else 0
        if secs <= 0:
            return
        durs = dict(self.app.settings["spell_durations"] or {})
        durs[spell.lower()] = secs
        self.app.settings["spell_durations"] = durs
        self.app.settings.save()
        # Fix the running timers for this spell too.
        for tm in self.app.engine.timers.timers.values():
            if tm.note == spell:
                tm.end = tm.start + secs
        self.app.engine.timers.version += 1

    def clear_spell_duration(self, spell):
        durs = dict(self.app.settings["spell_durations"] or {})
        durs.pop(spell.lower(), None)
        self.app.settings["spell_durations"] = durs
        self.app.settings.save()
        for sp in self.app.spells.by_name.get(spell.lower(), [])[:1]:
            secs = self.app.engine.spell_seconds(sp)
            for tm in self.app.engine.timers.timers.values():
                if tm.note == spell:
                    tm.end = tm.start + secs

    @staticmethod
    def _group(t):
        if t.kind == "spawn":
            return (3, f"Respawns: {t.target}")
        if t.kind == "recast":
            return (2, "Recast")
        if t.kind == "custom":
            return (2, "Custom")
        if t.kind == "boat":
            return (2, f"Boat: {t.target}")
        if t.kind == "ringwar":
            return (2, t.target)
        if t.kind == "ch":
            return (2, f"CH chain: {t.target}")
        if t.target == "You":
            return (0, "You")
        return (1, t.target)

    def refresh(self, now):
        c = self.c
        c.delete("all")
        self.rows = []
        f, fb = self.app.fonts["normal"], self.app.fonts["bold"]
        rh = f.metrics("linespace") + 8
        self._row_h = rh
        w = max(c.winfo_width(), 120)
        view = self._view_h = max(c.winfo_height(), 40)
        self._solid_canvas = []
        timers = [t for t in self.app.engine.timers.active(now) if self.wants(t)]
        self._set_auto_hidden(not timers and self.passthrough)   # play mode: no timers, no window
        if not timers:
            self._content_h = 0
            self.scroll_y = 0.0
            if not self.passthrough:     # in play mode an empty list shows nothing at all
                item = c.create_text(w / 2, 24, text=self.empty_text, fill=P["dim"], font=f)
                box = c.bbox(item)
                if box:
                    self._solid_canvas.append((box[0] - 2, box[1] - 2,
                                               box[2] - box[0] + 4, box[3] - box[1] + 4))
            self._update_scrollbar()
            self._fit()
            self.sync_solid()
            return
        groups = {}
        for t in timers:
            groups.setdefault(self._group(t), []).append(t)
        order = sorted(groups)
        # how tall the whole list is, so the scroll position can be kept in range
        self._content_h = 4 + sum((rh - 2) + len(groups[k]) * rh + 4 for k in order)
        self._clamp_scroll()
        y = 4 - self.scroll_y
        for k in order:
            gname = k[1]
            if y + rh > 0 and y < view:                # only draw what can be seen
                c.create_text(6, y, anchor="nw", text=gname, fill=P["accent"], font=fb)
                self._solid_canvas.append((4, y, fb.measure(gname) + 6, fb.metrics("linespace")))
            y += rh - 2
            for t in groups[k]:
                y1 = y + rh - 3
                if y1 > 0 and y < view:
                    rem = t.end - now
                    frac = max(0.0, min(1.0, rem / t.duration))
                    c.create_rectangle(4, y, w - 4, y1, fill=P["panel"], outline="")
                    color = P["up"] if (t.kind == "spawn" and rem <= 0) else KIND_COLOR.get(t.kind, P["buff"])
                    if frac > 0:
                        c.create_rectangle(4, y, 4 + (w - 8) * frac, y1, fill=color, outline="")
                    elif t.kind == "spawn":
                        c.create_rectangle(4, y, w - 4, y1, fill=color, outline="")
                    mid = (y + y1) / 2
                    right = "UP" if rem <= 0 else fmt_secs(rem)
                    c.create_text(w - 10, mid, anchor="e", text=right, fill=P["text"], font=fb)
                    c.create_text(10, mid, anchor="w", text=t.label, fill=P["text"], font=f,
                                  width=max(40, w - 90))
                    self.rows.append((y, y1, t.key))
                    self._solid_canvas.append((4, y, w - 8, y1 - y))
                y += rh
            y += 4
        self._update_scrollbar()
        self._fit()
        self.sync_solid()

    def _update_scrollbar(self):
        """Show the scrollbar only while the list is longer than its window."""
        need = self._content_h > self._view_h + 1
        try:
            changed = need != self._vbar_shown
            if need and not self._vbar_shown:
                self.vbar.pack(side="right", fill="y", pady=4, before=self.c)
                self._vbar_shown = True
            elif not need and self._vbar_shown:
                self.vbar.pack_forget()
                self._vbar_shown = False
                self.hold_ghost = False
            if changed:        # the clickable strip appeared or went: tell the X server once it is laid out
                self.win.after(120, self.apply_input)
            if need:
                total = float(self._content_h)
                self.vbar.set(self.scroll_y / total, min(1.0, (self.scroll_y + self._view_h) / total))
        except tk.TclError:
            pass


class MobTimersWindow(TimersWindow):
    """Your debuffs on mobs (root, mez, slows, DoTs)."""
    name, title, default_geometry = "mobtimers", "Mob Timers", "280x260+40+560"
    empty_text = "No debuffs on mobs"
    default_view_height = 200

    def wants(self, t):
        return is_mob_timer(t, self.app.settings)

    def build_toolbar(self, bar):
        ttk.Button(bar, text="Clear", style="Tool.TButton", command=self.clear).pack(side="left")
        self.debuffs_var = tk.BooleanVar(value=bool(self.app.settings["mob_timers_debuffs"]))
        ttk.Checkbutton(bar, text="Debuffs here", variable=self.debuffs_var,
                        command=self._debuffs_toggled).pack(side="right")

    def _debuffs_toggled(self):
        self.app.settings["mob_timers_debuffs"] = bool(self.debuffs_var.get())
        self.app.settings.save()


class RespawnTimersWindow(TimersWindow):
    """Respawn timers for the mobs you have killed, grouped by zone."""
    name, title, default_geometry = "respawns", "Respawn Timers", "280x300+340+560"
    empty_text = "No respawn timers"
    default_view_height = 220

    def wants(self, t):
        return is_spawn_timer(t)

    def build_toolbar(self, bar):
        ttk.Button(bar, text="Add respawn", style="Tool.TButton",
                   command=self.add_respawn).pack(side="left")
        ttk.Button(bar, text="Clear", style="Tool.TButton", command=self.clear).pack(side="left", padx=4)
        ttk.Label(bar, text="right-click to restart", style="Dim.TLabel",
                  font=self.app.fonts["small"]).pack(side="right")

    def add_respawn(self):
        import time
        eng = self.app.engine
        name = simpledialog.askstring("Respawn timer", "Mob name:", parent=self.win,
                                      initialvalue=eng.last_con or "")
        if not name:
            return
        default = fmt_secs(eng.spawn_seconds(name)[0])
        dur = simpledialog.askstring("Respawn timer", f"Respawn time for {name} (mm:ss):",
                                     parent=self.win, initialvalue=default)
        secs = parse_hms(dur)
        if secs > 0:
            eng.start_spawn_timer(name, float(secs), time.time())


# ========================================================================= dps
class DpsWindow(OverlayWindow):
    name, title, default_geometry = "dps", "DPS Meter", "320x260+340+120"

    def build(self):
        self.pinned = None  # fight id the user stepped to, None = follow latest
        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Button(bar, text="\u25c0", width=3, style="Tool.TButton",
                   command=lambda: self.step(-1)).pack(side="left")
        ttk.Button(bar, text="\u25b6", width=3, style="Tool.TButton",
                   command=lambda: self.step(1)).pack(side="left", padx=(2, 4))
        ttk.Button(bar, text="Latest", style="Tool.TButton",
                   command=self.follow_latest).pack(side="left")
        ttk.Button(bar, text="Copy", style="Tool.TButton", command=self.copy).pack(side="right")
        self.c = self.canvas(self.win)
        self.c.pack(fill="both", expand=True, padx=2, pady=4)
        self.draggable(bar, self.c)

    def _current(self):
        fights = self.app.engine.dps.fights()
        if not fights:
            return None, fights
        if self.pinned is not None:
            for fgt in fights:
                if fgt.fid == self.pinned:
                    return fgt, fights
            self.pinned = None
        return fights[-1], fights

    def step(self, d):
        cur, fights = self._current()
        if not cur:
            return
        i = max(0, min(len(fights) - 1, fights.index(cur) + d))
        self.pinned = None if i == len(fights) - 1 else fights[i].fid

    def follow_latest(self):
        self.pinned = None

    def copy(self):
        cur, _ = self._current()
        if cur:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(cur.summary())

    def refresh(self, now):
        c = self.c
        c.delete("all")
        f, fb, fs = self.app.fonts["normal"], self.app.fonts["bold"], self.app.fonts["small"]
        rh = f.metrics("linespace") + 8
        w = max(c.winfo_width(), 140)
        cur, fights = self._current()
        if not cur:
            c.create_text(w / 2, 24, text="No fights yet", fill=P["dim"], font=f)
            return
        state = "ended" if cur.ended else "active"
        idx = fights.index(cur) + 1
        c.create_text(6, 4, anchor="nw", text=cur.target, fill=P["accent"], font=fb,
                      width=w - 12)
        head = (f"{int(cur.duration)}s {state}   total {cur.total:,}   "
                f"{cur.total / cur.duration:,.1f} dps   taken {cur.taken:,}   ({idx}/{len(fights)})")
        c.create_text(6, 4 + rh - 4, anchor="nw", text=head, fill=P["dim"], font=fs, width=w - 12)
        y = 4 + 2 * rh
        rows = cur.rows()
        top = rows[0][1] if rows else 1
        for name, dmg, dps, pct in rows:
            y1 = y + rh - 3
            c.create_rectangle(4, y, w - 4, y1, fill=P["panel"], outline="")
            color = P["you"] if name == "You" else P["other"]
            c.create_rectangle(4, y, 4 + (w - 8) * dmg / max(1, top), y1, fill=color, outline="")
            mid = (y + y1) / 2
            c.create_text(10, mid, anchor="w", text=name, fill=P["text"], font=fb)
            c.create_text(w - 10, mid, anchor="e", fill=P["text"], font=f,
                          text=f"{dmg:,}   {dps:,.1f}   {pct:.0f}%")
            y += rh
        c.configure(scrollregion=(0, 0, w, y))


# ========================================================================= mana sieve
class SieveWindow(OverlayWindow):
    """Enchanters: how many Mana Sieves have hit each mob (yours and everyone else's), to
    tell when it is out of mana. A mob's count goes when it dies."""
    name, title, default_geometry = "sieve", "Mana Sieve", "240x120+680+120"

    def build(self):
        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Button(bar, text="Clear", style="Tool.TButton",
                   command=lambda: self.app.engine.clear_sieves()).pack(side="left")
        self.c = self.canvas(self.win)
        self.c.pack(fill="both", expand=True, padx=2, pady=4)
        self.draggable(bar, self.c)

    def refresh(self, now):
        c = self.c
        c.delete("all")
        rows = self.app.engine.sieve_counts(now)
        self._set_auto_hidden(not rows and self.passthrough)   # play mode: nothing to count, no window
        f, fb = self.app.fonts["normal"], self.app.fonts["bold"]
        big = fb.copy()
        big.configure(size=int(fb.cget("size")) + 8)
        rh = max(big.metrics("linespace"), f.metrics("linespace")) + 10
        w = max(c.winfo_width(), 140)
        if not rows:
            if not self.passthrough:
                c.create_text(w / 2, 24, text="No mana sieves yet", fill=P["dim"], font=f)
            return
        y = 2
        for name, n, since_first, since_last in rows:
            y1 = y + rh - 3
            c.create_rectangle(4, y, w - 4, y1, fill=P["panel"], outline="")
            mid = (y + y1) / 2
            c.create_text(w - 10, mid, anchor="e", text=str(n), fill=P["accent"], font=big)
            c.create_text(10, mid - 7, anchor="w", text=name, fill=P["text"], font=fb,
                          width=max(40, w - 90))
            c.create_text(10, mid + 9, anchor="w", font=self.app.fonts["small"], fill=P["dim"],
                          text=f"sieves \u00b7 last {int(since_last)}s ago")
            y += rh
        c.configure(scrollregion=(0, 0, w, y))


# ========================================================================= map
LEVEL_MODES = [("hide", "Current level only"), ("dim", "Dim other levels"), ("all", "All levels")]


class MapWindow(OverlayWindow):
    name, title, default_geometry = "map", "Map", "520x520+660+120"
    always_top = True      # the map is never allowed to sit behind the game

    def build(self):
        self.md = None
        self.short = None
        self.map_status = "No zone detected yet."
        self._unmapped_zone = None
        self._available, self._labels = [], {}
        self.s, self.ox, self.oy = 1.0, 0.0, 0.0
        self._drag = None
        self.z_view_offset = 0          # levels above (+) or below (-) you that are being viewed
        self._level_key = None          # what the map items were last drawn for
        # Row 1: search. Type any part of a zone name, file name or shorthand.
        srow = ttk.Frame(self.win)
        srow.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(srow, text="Find zone").pack(side="left", padx=(2, 6))
        self.search_var = tk.StringVar()
        self.search = ttk.Entry(srow, textvariable=self.search_var)
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", self._on_search_key)
        self.search.bind("<Return>", lambda e: self._pick_result())
        self.search.bind("<Down>", lambda e: self._move_result(1))
        self.search.bind("<Up>", lambda e: self._move_result(-1))
        self.search.bind("<Escape>", lambda e: self._clear_search())
        self.search.bind("<FocusOut>", lambda e: self.win.after(200, self._hide_results))
        self.results = tk.Listbox(self.win, height=8, bg=P["panel"], fg=P["text"],
                                  selectbackground=P["accent"], selectforeground=P["bg"],
                                  highlightthickness=1, highlightcolor=P["line"],
                                  highlightbackground=P["line"], bd=0, activestyle="none",
                                  exportselection=False, font=self.app.fonts["normal"])
        self.results.bind("<ButtonRelease-1>", self._click_result)
        self._result_shorts = []
        self.win.bind("<Control-f>", lambda e: self.focus_search())

        # Row 2: full list, fit and view toggles.
        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=4, pady=(4, 0))
        self.zone_var = tk.StringVar()
        self._choices = {}
        self.combo = ttk.Combobox(bar, textvariable=self.zone_var, width=30, state="readonly")
        self.combo.pack(side="left")
        self.combo.bind("<<ComboboxSelected>>", self._on_combo)
        self.combo.bind("<Button-1>", lambda e: self.refresh_zone_list())
        ttk.Button(bar, text="Fit", style="Tool.TButton", command=self.draw).pack(side="left", padx=4)
        self.follow = tk.BooleanVar(value=bool(self.app.settings["map_follow"]))
        self.labels = tk.BooleanVar(value=bool(self.app.settings["map_labels"]))
        ttk.Checkbutton(bar, text="Follow", variable=self.follow,
                        command=self._save_toggles).pack(side="left")
        ttk.Checkbutton(bar, text="Labels", variable=self.labels,
                        command=self._labels_changed).pack(side="left", padx=4)
        # Mob names are the red labels (hostile NPCs) in the P99 maps; places, vendors and
        # quest NPCs keep their own colours, so they stay when this is off.
        self.mob_names = tk.BooleanVar(value=bool(self.app.settings["map_mob_names"]))
        ttk.Checkbutton(bar, text="Mob names", variable=self.mob_names,
                        command=self._labels_changed).pack(side="left")
        # Row 3: levels. Zones with several floors drawn on top of each other can show
        # only the floor you are on (z comes from /loc).
        lrow = ttk.Frame(self.win)
        lrow.pack(fill="x", padx=4, pady=(3, 0))
        ttk.Label(lrow, text="Levels").pack(side="left", padx=(2, 4))
        self.level_mode_var = tk.StringVar()
        self.level_combo = ttk.Combobox(lrow, textvariable=self.level_mode_var, width=17,
                                        state="readonly", values=[t for _, t in LEVEL_MODES])
        self.level_combo.pack(side="left")
        self.level_combo.bind("<<ComboboxSelected>>", lambda e: self._level_mode_changed())
        ttk.Button(lrow, text="\u25b2", width=2, style="Tool.TButton",
                   command=lambda: self._shift_level(+1)).pack(side="left", padx=(6, 1))
        ttk.Button(lrow, text="\u25bc", width=2, style="Tool.TButton",
                   command=lambda: self._shift_level(-1)).pack(side="left", padx=1)
        ttk.Button(lrow, text="Me", width=3, style="Tool.TButton",
                   command=self._level_reset).pack(side="left", padx=(1, 6))
        ttk.Label(lrow, text="Level height").pack(side="left", padx=(0, 3))
        self.height_var = tk.StringVar(value="10")
        self.height_spin = ttk.Spinbox(lrow, from_=2, to=200, increment=1, width=5,
                                       textvariable=self.height_var,
                                       command=self._height_changed)
        self.height_spin.pack(side="left")
        for ev in ("<Return>", "<FocusOut>"):
            self.height_spin.bind(ev, lambda e: self._height_changed())
        self.level_label = ttk.Label(self.win, style="Dim.TLabel", font=self.app.fonts["small"])
        self.level_label.pack(fill="x", padx=6)
        self.c = self.canvas(self.win)
        self.c.pack(fill="both", expand=True, padx=2, pady=2)
        self.status = ttk.Label(self.win, style="Dim.TLabel", font=self.app.fonts["small"],
                                text="Type /loc (or bind it to a hotkey) to update your position.")
        self.status.pack(fill="x", padx=6, pady=(0, 4))
        self.draggable(srow, bar, lrow, self.level_label, self.status)
        # Unmanaged windows don't get keyboard focus by themselves.
        self.search.bind("<Button-1>", lambda e: self.search.focus_force(), add="+")
        c = self.c
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._motion)
        c.bind("<Button-4>", lambda e: self._wheel(e, 1.25))
        c.bind("<Button-5>", lambda e: self._wheel(e, 0.8))
        c.bind("<MouseWheel>", lambda e: self._wheel(e, 1.25 if e.delta > 0 else 0.8))
        self._last_size = (0, 0)
        c.bind("<Configure>", self._on_resize)
        self.refresh_zone_list()

    # -- data
    @property
    def map_dirs(self):
        return maps.find_map_dirs(self.app.settings["eq_dir"])

    def refresh_zone_list(self):
        try:
            self._available = maps.available_maps(self.map_dirs)
            choices = maps.zone_choices(self._available)
            self._choices = dict(choices)
            self._labels = {short: label for label, short in choices}
            self.combo["values"] = [label for label, _ in choices]
        except Exception:
            log.exception("listing maps failed")

    def _on_combo(self, _e=None):
        short = self._choices.get(self.zone_var.get())
        if short:
            self.load(short)

    # -- search
    def focus_search(self):
        self.search.focus_force()
        self.search.select_range(0, "end")

    def _on_search_key(self, e):
        if e.keysym in ("Up", "Down", "Return", "KP_Enter", "Escape", "Tab"):
            return
        q = self.search_var.get()
        if not getattr(self, "_available", None):
            self.refresh_zone_list()
        self._result_shorts = maps.search_zones(q, self._available, limit=10) if q.strip() else []
        lb = self.results
        lb.delete(0, "end")
        if not self._result_shorts:
            if q.strip():
                lb.insert("end", "No matching zone")
                self._show_results(1)
            else:
                self._hide_results()
            return
        for short in self._result_shorts:
            lb.insert("end", self._labels.get(short, short))
        lb.selection_clear(0, "end")
        lb.selection_set(0)
        self._show_results(len(self._result_shorts))

    def _show_results(self, n):
        self.results.configure(height=min(10, max(1, n)))
        self.results.place(in_=self.search, relx=0, rely=1, relwidth=1, y=2)
        self.results.lift()

    def _hide_results(self):
        self.results.place_forget()

    def _move_result(self, d):
        if not self._result_shorts:
            return "break"
        sel = self.results.curselection()
        i = (sel[0] if sel else -1) + d
        i = max(0, min(len(self._result_shorts) - 1, i))
        self.results.selection_clear(0, "end")
        self.results.selection_set(i)
        self.results.see(i)
        return "break"

    def _pick_result(self, index=None):
        if not self._result_shorts:
            return "break"
        if index is None:
            sel = self.results.curselection()
            index = sel[0] if sel else 0
        if 0 <= index < len(self._result_shorts):
            short = self._result_shorts[index]
            self._clear_search()
            self.load(short)
            self.c.focus_set()
        return "break"

    def _click_result(self, e):
        self._pick_result(self.results.nearest(e.y))

    def _clear_search(self):
        self.search_var.set("")
        self._result_shorts = []
        self._hide_results()

    def _set_status(self, text):
        self.map_status = text
        self.status.configure(text=text)

    def show(self):
        super().show()
        self.win.after(60, self.draw)  # size is only known once it's on screen

    def on_zone(self, long_name):
        dirs = self.map_dirs
        if self.app.settings["map_auto_show"] and not self.is_visible():
            self.show()
            self.app.on_window_toggled()
        if not dirs:
            self.md, self.short = None, None
            self.draw()
            self._set_status(f"{long_name}: no map files found (the bundled maps_data "
                             "folder is missing; re-extract the app).")
            return
        avail = maps.available_maps(dirs)
        short = maps.short_name(long_name, self.app.settings["zone_aliases"], avail)
        log.info("zone %r -> map %r", long_name, short)
        if short and self.load(short, remember_for=None):
            return
        self.md, self.short = None, None
        self.zone_var.set("")
        self.draw()
        self._unmapped_zone = long_name
        why = f"no map file '{short}.txt'" if short else "zone name not recognized"
        self._set_status(f"{long_name}: {why}. Search or pick the right map and "
                         "it'll be used for this zone from now on.")

    def load(self, short, remember_for="auto"):
        md = maps.load_map(self.map_dirs, short)
        if not md:
            self._set_status(f"Could not load map '{short}'.")
            return False
        # A manual pick while the current zone had no map: remember it.
        zone = getattr(self, "_unmapped_zone", None) if remember_for == "auto" else None
        if zone:
            aliases = dict(self.app.settings["zone_aliases"] or {})
            aliases[zone] = short
            self.app.settings["zone_aliases"] = aliases
            self.app.settings.save()
        self._unmapped_zone = None
        self.md, self.short = md, short
        if not getattr(self, "_labels", None):
            self.refresh_zone_list()
        self.zone_var.set(self._labels.get(short, short))
        tip = "" if self.app.engine.loc else "  Type /loc to show your position."
        self._set_status(f"{short}: scroll to zoom, drag to pan.{tip}")
        self._enter_zone_levels()
        self.draw()
        return True

    # -- geometry
    def world_player(self):
        loc = self.app.engine.loc
        if not loc:
            return None
        y, x = loc[0], loc[1]
        return (-x, -y) if self.app.settings["map_negate_loc"] else (x, y)

    def to_screen(self, wx, wy):
        return wx * self.s + self.ox, wy * self.s + self.oy

    @staticmethod
    def _color(rgb):
        r, g, b = (max(0, min(255, v)) for v in rgb)
        if r + g + b < 150:  # dark lines vanish on our dark background
            return "#aeb7c4"
        return f"#{r:02x}{g:02x}{b:02x}"

    def _fit(self, lines, lh, w, h):
        """Grow the banner (never shrink it) if the text no longer fits."""
        try:
            widest = max(self.font.measure(t) for t in lines) + 40
            need_w = min(max(w, widest), max(400, self.app.root.winfo_screenwidth() or 1920))
            need_h = lh * len(lines) + (50 if self.positioning else 20)
            if need_w > w + 2 or need_h > h + 2:
                self.win.geometry(f"{int(max(w, need_w))}x{int(max(h, need_h))}")
        except (tk.TclError, TypeError, ValueError):
            pass

    def draw(self):
        c = self.c
        c.delete("all")
        W, H = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        self._last_size = (W, H)
        if not self.md:
            c.create_text(W / 2, H / 2, text="No map loaded", fill=P["dim"], font=self.app.fonts["normal"])
            return
        x0, y0, x1, y1 = self.md.bounds      # the fit uses every level, so the view never jumps
        self.s = min((W - 20) / max(1e-6, x1 - x0), (H - 20) / max(1e-6, y1 - y0))
        self.ox = (W - (x1 - x0) * self.s) / 2 - x0 * self.s
        self.oy = (H - (y1 - y0) * self.s) / 2 - y0 * self.s
        self._level_key = None
        self._refresh_levels(force=True)
        self.draw_player(center=False)

    # -- levels (z axis)
    def _zone_levels(self):
        """(mode, height) for this zone: your choice for it, else EQTool's defaults
        (all levels for the zones it flags as single-level, current level otherwise)."""
        show_all, height = maps.level_info(self.short)
        ov = (self.app.settings["map_level_zones"] or {}).get(self.short or "", {})
        mode = ov.get("mode") or ("all" if show_all else "hide")
        try:
            h = float(ov.get("height") or height)
        except (TypeError, ValueError):
            h = height
        return mode, max(1.0, h)

    def _floors(self, h):
        """The zone's detected floors, cached per map and level height."""
        key = (id(self.md), h)
        if getattr(self, "_floors_key", None) != key:
            self._floors_key = key
            self._floors_cache = maps.level_centers(self.md, h) if self.md else []
        return self._floors_cache

    def _my_floor_index(self, floors, z):
        return min(range(len(floors)), key=lambda i: abs(floors[i] - z)) if floors else 0

    def _ref_z(self, h):
        """The height the map is drawn for: yours, or the floor you stepped to with the
        arrows (the offset counts floors from the one you are on)."""
        loc = self.app.engine.loc
        if not loc:
            return None
        floors = self._floors(h)
        if self.z_view_offset == 0:
            return loc[2]
        if not floors:
            return loc[2] + self.z_view_offset * h
        i = max(0, min(len(floors) - 1, self._my_floor_index(floors, loc[2]) + self.z_view_offset))
        return floors[i]

    @staticmethod
    def _blend(hexcolor, o):
        """Fade a colour toward the window background (Tk has no per-item alpha)."""
        if o >= 0.999:
            return hexcolor
        bg = P["bg"]
        out = []
        for i in (1, 3, 5):
            b, c = int(bg[i:i + 2], 16), int(hexcolor[i:i + 2], 16)
            out.append(int(b + (c - b) * o))
        return "#%02x%02x%02x" % tuple(out)

    @staticmethod
    def _item_opacity(dz, mode, h):
        if mode == "all":
            return 1.0
        if mode == "hide":
            return 1.0 if dz < h else 0.0
        return maps.level_opacity(dz, h)

    def _refresh_levels(self, force=False):
        """Redraw the map items if what should be shown has changed."""
        if not self.md:
            return
        mode, h = self._zone_levels()
        ref = self._ref_z(h)
        key = (mode, h, None if ref is None else round(ref))
        if force or key != self._level_key:
            self._level_key = key
            self._draw_items(mode, h, ref)
        self._update_level_label(mode, h, ref)

    def _draw_items(self, mode, h, ref):
        c = self.c
        c.delete("map")
        if not self.md:
            return
        lines = []
        for ax, ay, bx, by, rgb, z1, z2 in self.md.lines:
            o = 1.0 if ref is None else self._item_opacity(maps.line_dz(z1, z2, ref), mode, h)
            if o > 0:
                lines.append((o, ax, ay, bx, by, rgb))
        lines.sort(key=lambda t: t[0])          # faint ones first: your level ends up on top
        for o, ax, ay, bx, by, rgb in lines:
            sx1, sy1 = self.to_screen(ax, ay)
            sx2, sy2 = self.to_screen(bx, by)
            c.create_line(sx1, sy1, sx2, sy2, fill=self._blend(self._color(rgb), o), tags=("map",))
        if self.labels.get():
            fs = self.app.fonts["small"]
            show_mobs = self.mob_names.get()
            for px, py, rgb, label, z in self.md.points:
                if not show_mobs and maps.is_mob_label(rgb):
                    continue
                o = 1.0 if ref is None else self._item_opacity(abs(z - ref), mode, h)
                if o > 0:
                    sx, sy = self.to_screen(px, py)
                    c.create_text(sx, sy, text=label, fill=self._blend(self._color(rgb), o),
                                  font=fs, tags=("map",))

    def _update_level_label(self, mode, h, ref):
        if mode == "all":
            text = "Showing every level of this zone."
        elif ref is None:
            text = "Type /loc so the map knows which level you are on (showing everything until then)."
        else:
            floors = self._floors(h)
            off = ""
            if self.z_view_offset:
                i = max(0, min(len(floors) - 1, self._my_floor_index(floors, self.app.engine.loc[2])
                               + self.z_view_offset)) if floors else None
                off = (f"  Viewing floor {i + 1} of {len(floors)}." if floors else
                       f"  Viewing {self.z_view_offset:+d} level(s).")
            text = (f"Your height z={self.app.engine.loc[2]:.0f}.  Showing z {ref - h:.0f} to "
                    f"{ref + h:.0f}" + ("" if mode == "hide" else ", others dimmed") + "." + off)
        self.level_label.configure(text=text)

    def _enter_zone_levels(self):
        """A map was loaded: show this zone's level settings in the controls."""
        self.z_view_offset = 0
        mode, h = self._zone_levels()
        self.level_mode_var.set(dict(LEVEL_MODES)[mode])
        self.height_var.set(f"{h:g}")

    def _store_level_setting(self, **kw):
        if not self.short:
            return
        zones = dict(self.app.settings["map_level_zones"] or {})
        ov = dict(zones.get(self.short, {}))
        ov.update(kw)
        zones[self.short] = ov
        self.app.settings["map_level_zones"] = zones
        self.app.settings.save()

    def _level_mode_changed(self):
        mode = next((k for k, t in LEVEL_MODES if t == self.level_mode_var.get()), None)
        if mode:
            self._store_level_setting(mode=mode)
            self._refresh_levels(force=True)
            self.draw_player(center=False)

    def _height_changed(self):
        try:
            h = float(self.height_var.get())
        except ValueError:
            return
        if h >= 1 and self.short:
            self._store_level_setting(height=h)
            self._refresh_levels(force=True)
            self.draw_player(center=False)

    def _shift_level(self, d):
        if not self.app.engine.loc:
            self.level_label.configure(text="Type /loc first: the map needs your height to move up or down from.")
            return
        _mode, h = self._zone_levels()
        floors = self._floors(h)
        if floors:       # never step past the top or bottom floor
            mine = self._my_floor_index(floors, self.app.engine.loc[2])
            self.z_view_offset = max(-mine, min(len(floors) - 1 - mine, self.z_view_offset + d))
        else:
            self.z_view_offset += d
        self._refresh_levels(force=True)
        self.draw_player(center=False)

    def _level_reset(self):
        self.z_view_offset = 0
        self._refresh_levels(force=True)
        self.draw_player(center=False)

    def draw_player(self, center=None):
        c = self.c
        c.delete("player")
        if not self.md:
            return
        pos = self.world_player()
        if not pos:
            return
        if center is None:
            center = self.follow.get()
        sx, sy = self.to_screen(*pos)
        if center:
            W, H = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
            dx, dy = W / 2 - sx, H / 2 - sy
            c.move("map", dx, dy)
            self.ox += dx
            self.oy += dy
            sx, sy = W / 2, H / 2
        neg = self.app.settings["map_negate_loc"]
        pts = []
        for ly, lx, _ in self.app.engine.trail:
            wx, wy = (-lx, -ly) if neg else (lx, ly)
            pts.append(self.to_screen(wx, wy))
        for tx, ty in pts[:-1]:
            c.create_oval(tx - 2, ty - 2, tx + 2, ty + 2, fill=P["dim"], outline="", tags=("player",))
        if len(pts) >= 2:
            (ax, ay), (bx, by) = pts[-2], pts[-1]
            dx, dy = bx - ax, by - ay
            n = (dx * dx + dy * dy) ** 0.5
            if n > 0.5:
                c.create_line(sx, sy, sx + dx / n * 16, sy + dy / n * 16, fill=P["accent"],
                              width=2, arrow="last", tags=("player",))
        c.create_oval(sx - 6, sy - 6, sx + 6, sy + 6, fill=P["accent"], outline=P["text"],
                      width=2, tags=("player",))

    # -- interaction
    def _press(self, e):
        self._drag = (e.x, e.y)

    def _motion(self, e):
        if not self._drag:
            return
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        self._drag = (e.x, e.y)
        self.c.move("map", dx, dy)
        self.ox += dx
        self.oy += dy
        if self.follow.get():
            self.follow.set(False)
            self._save_toggles()
        self.draw_player(center=False)

    def _wheel(self, e, f):
        if e.state & 0x4:  # Ctrl held: that's the opacity shortcut, not zoom
            return
        self._zoom(e.x, e.y, f)

    def _zoom(self, x, y, f):
        if not self.md:
            return
        self.c.scale("map", x, y, f, f)
        self.s *= f
        self.ox = x + (self.ox - x) * f
        self.oy = y + (self.oy - y) * f
        self.draw_player(center=False)

    def _on_resize(self, e):
        if (e.width, e.height) != self._last_size and abs(e.width - self._last_size[0]) + abs(
                e.height - self._last_size[1]) > 4:
            self.draw()

    def _save_toggles(self):
        self.app.settings["map_follow"] = bool(self.follow.get())
        self.app.settings["map_labels"] = bool(self.labels.get())
        self.app.settings["map_mob_names"] = bool(self.mob_names.get())

    def _labels_changed(self):
        self._save_toggles()
        self._refresh_levels(force=True)     # redraw the items only: keep your zoom and pan
        self.draw_player(center=False)

    def on_loc(self):
        self._refresh_levels()       # your height may have crossed into another level
        self.draw_player()
        if self.md and self.map_status.endswith("Type /loc to show your position."):
            self._set_status(f"{self.short}: scroll to zoom, drag to pan.")


# ==================================================================== mob info
class MobWindow(OverlayWindow):
    name, title, default_geometry = "mob", "Mob Info", "340x440+1190+120"

    def build(self):
        self.info = None
        self.current = None
        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=4, pady=(4, 0))
        self.q = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.q)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self.lookup(self.q.get(), force=True))
        ent.bind("<Button-1>", lambda e: ent.focus_force(), add="+")
        ttk.Button(bar, text="Look up", style="Tool.TButton",
                   command=lambda: self.lookup(self.q.get(), force=True)).pack(side="left", padx=(4, 0))
        self.head = ttk.Label(self.win, text="/consider a mob to look it up",
                              style="Head.TLabel", font=self.app.fonts["bold"])
        self.head.pack(fill="x", padx=6, pady=(6, 2))
        self.text = tk.Text(self.win, wrap="word", bg=P["panel"], fg=P["text"], bd=0,
                            highlightthickness=0, padx=8, pady=6, font=self.app.fonts["normal"])
        self.text.tag_configure("k", foreground=P["accent"], font=self.app.fonts["bold"])
        self.text.pack(fill="both", expand=True, padx=4, pady=2)
        self.text.configure(state="disabled")
        btns = ttk.Frame(self.win)
        btns.pack(fill="x", padx=4, pady=4)
        ttk.Button(btns, text="Open wiki", style="Tool.TButton", command=self.open_wiki).pack(side="left")
        ttk.Button(btns, text="Start respawn timer", style="Tool.TButton",
                   command=self.start_spawn).pack(side="left", padx=4)
        ttk.Button(btns, text="Set respawn...", style="Tool.TButton",
                   command=self.set_rule).pack(side="left")
        self.draggable(bar, self.head, btns)

    def _set_text(self, pairs, plain=None):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if plain:
            t.insert("end", plain)
        for k, v in pairs:
            t.insert("end", k + "\n", ("k",))
            t.insert("end", v + "\n\n")
        t.configure(state="disabled")

    def lookup(self, name, force=False):
        name = (name or "").strip()
        if not name:
            return
        self.current = name
        self.q.set(name)
        self.head.configure(text=name)
        self._set_text([], plain="Looking up on the P99 wiki...")
        self.app.mobinfo.request(name, force=force)

    def on_con(self, mob):
        if mob and (not self.current or mob.lower() != self.current.lower()):
            self.lookup(mob)

    def on_result(self, info):
        if not self.current or info.query.lower() != self.current.lower():
            return
        self.info = info
        rule = (self.app.settings["spawn_rules"] or {}).get(info.query.lower())
        extra = [("Your respawn timer", fmt_secs(rule))] if rule else []
        if info.error:
            self.head.configure(text=info.query)
            self._set_text(extra, plain=f"{info.error}.\n\n'Open wiki' searches the wiki for it.\n\n")
        else:
            self.head.configure(text=info.title or info.query)
            self._set_text(extra + info.fields)

    def open_wiki(self):
        url = self.info.url if self.info else None
        if not url and self.current:
            from ..mobinfo import WIKI
            import urllib.parse
            url = f"{WIKI}/index.php?search={urllib.parse.quote(self.current)}"
        if url:
            webbrowser.open(url)

    def _respawn_for(self, name):
        """Your rule, else EQTool's table for this NPC / zone, else the wiki."""
        return int(self.app.engine.spawn_seconds(name)[0])

    def start_spawn(self):
        name = self.current or self.app.engine.last_con
        if not name:
            return
        import time
        self.app.engine.start_spawn_timer(name, float(self._respawn_for(name)), time.time())

    def set_rule(self):
        name = self.current or self.app.engine.last_con
        if not name:
            return
        cur = fmt_secs(self._respawn_for(name))
        val = simpledialog.askstring(
            "Respawn time", f"Respawn time for {name} (mm:ss or h:mm:ss).\n"
            "Kills of this mob will start a timer automatically. Empty removes it.",
            initialvalue=cur, parent=self.win)
        if val is None:
            return
        rules = dict(self.app.settings["spawn_rules"] or {})
        secs = parse_hms(val)
        if secs > 0:
            rules[name.lower()] = secs
        else:
            rules.pop(name.lower(), None)
        self.app.settings["spawn_rules"] = rules
        self.app.settings.save()
        if self.info:
            self.on_result(self.info)


# ====================================================================== alerts
class AlertWindow:
    """The big yellow banner.

    The text lives in its own fully solid window, just big enough for the text, so
    it is always 100% opaque. Behind it sits a separate translucent panel, and the
    banner's opacity slider controls only that panel. (Tk can only fade a whole
    window, which would fade the text with it.) Both are click-through and hidden
    when there is nothing to say. `self.win` is only the frame you drag and size
    while positioning the banner, and the place the banner is centred on."""

    name = "alerts"
    SAMPLE = "CHARM BREAK"
    BG = "#0d1117"
    PAD_X, PAD_Y = 18, 10     # room around the text inside its window
    HALO = 14                 # how far the translucent panel reaches past the text

    def __init__(self, app):
        self.app = app
        self.alerts = []          # [(text, expires_at)]
        self.positioning = False
        self.shown = False
        self._ghosted = False
        self._ghost_rect = None
        self._back_on = False
        self.win = tk.Toplevel(app.root)
        self.win.title("Alerts - Everlast")
        self.win.configure(bg=self.BG)
        self.win.protocol("WM_DELETE_WINDOW", self.stop_positioning)
        conf = (app.settings["windows"] or {}).get(self.name, {})
        geo = conf.get("geometry")
        if not geo:
            sw = max(800, app.root.winfo_screenwidth() or 1920)
            geo = f"900x240+{(sw - 900) // 2}+140"
        try:
            self.win.geometry(geo)
        except tk.TclError:
            self.win.geometry("900x240+200+140")
        self.c = tk.Canvas(self.win, bg=self.BG, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)
        self.c.bind("<ButtonPress-1>", self._press)
        self.c.bind("<B1-Motion>", self._drag)
        # Live text size while positioning: mouse wheel or +/- keys.
        self.c.bind("<Button-4>", lambda e: self.bump_size(+4))
        self.c.bind("<Button-5>", lambda e: self.bump_size(-4))
        self.c.bind("<MouseWheel>", lambda e: self.bump_size(4 if e.delta > 0 else -4))
        for key in ("<plus>", "<equal>", "<KP_Add>"):
            self.win.bind(key, lambda e: self.bump_size(+4))
        for key in ("<minus>", "<KP_Subtract>"):
            self.win.bind(key, lambda e: self.bump_size(-4))
        self.c.bind("<Configure>", lambda e: self.draw() if self.positioning else None)
        self._drag_from = None
        # The banner itself: a solid text window and a translucent panel behind it.
        self.tw = tk.Toplevel(app.root)
        self.tw.withdraw()
        self.tw.configure(bg=self.BG)
        self.tc = tk.Canvas(self.tw, bg=self.BG, highlightthickness=0, bd=0)
        self.tc.pack(fill="both", expand=True)
        self.back = tk.Toplevel(app.root)
        self.back.withdraw()
        self.back.configure(bg=self.BG)
        self.back_fill = tk.Canvas(self.back, bg=self.BG, highlightthickness=0, bd=0)
        self.back_fill.pack(fill="both", expand=True)       # paints the panel itself (see Backdrop)
        for w in (self.tw, self.back):
            try:
                w.overrideredirect(True)
            except tk.TclError:
                pass
        self.font = None
        self.apply_prefs()
        self._hide()

    # -- appearance
    def apply_prefs(self):
        import tkinter.font as tkfont
        size = int(self.app.settings.num("alert_font_size", 48))
        try:
            fam = self.app.fonts["bold"].actual("family") or "TkDefaultFont"
        except Exception:
            fam = "TkDefaultFont"
        if self.font is None:
            self.font = tkfont.Font(self.app.root, family=fam, size=size, weight="bold")
        else:
            self.font.configure(size=size)
        for w, alpha in ((self.win, 1.0), (self.tw, 1.0), (self.back, self.back_alpha())):
            try:
                w.attributes("-alpha", alpha)
            except tk.TclError:
                pass
            self.app.decorations.set_opacity(w, alpha)
        try:
            self.win.attributes("-topmost", True)
        except tk.TclError:
            pass
        if self.shown and not self.positioning and self.alerts:
            self._draw_alert()           # the panel's opacity or the text size changed

    def back_alpha(self):
        """Opacity of the panel behind the text (the banner's slider). The text is never faded."""
        return max(0.0, min(1.0, self.app.settings.num("alert_opacity", 0.85)))

    def _set_borderless(self, flag):
        try:
            self.win.overrideredirect(bool(flag))
        except tk.TclError:
            pass

    def _hide(self):
        self._ghosted = False
        for w in (self.tw, self.back, self.win):
            try:
                w.withdraw()
            except tk.TclError:
                pass
        self._back_on = False
        self._set_borderless(True)
        self.shown = False

    # -- hover-hide (the Wayland workaround, as for the overlays)
    def _rect(self):
        return (self.tw.winfo_rootx(), self.tw.winfo_rooty(),
                self.tw.winfo_width(), self.tw.winfo_height())

    def update_ghost(self, px, py, now):
        """While the mouse is over the banner, hide it so the click reaches the game."""
        try:
            if self.positioning or not self.shown or not self.app.ghost_enabled():
                if self._ghosted:
                    self._unghost()
                return
            if not self._ghosted:
                if OverlayWindow._inside(self._rect(), px, py, self.HALO):
                    self._ghost_rect = self._rect()
                    self._ghosted = True
                    self.tw.withdraw()
                    self.back.withdraw()
                    self._back_on = False
            elif not OverlayWindow._inside(self._ghost_rect, px, py,
                                           OverlayWindow.GHOST_MARGIN + self.HALO):
                self._unghost()
        except tk.TclError:
            pass

    def _unghost(self):
        self._ghosted = False
        self.tw.deiconify()
        if self.alerts:
            self._draw_alert()
        self._stack()

    def _show(self):
        if not self.shown:
            self.shown = True
            self.tw.deiconify()
            self.app.clickthrough.set(self.tw, True)
            self.tw.after(80, lambda: None if self.positioning else self.app.clickthrough.set(self.tw, True))
        self._stack()

    def _stack(self):
        """Above everything, with the panel just behind the text."""
        try:
            self.tw.lift()
            if self._back_on:
                self.back.lower(self.tw)
        except tk.TclError:
            pass

    def maintain(self):
        """Once a second: re-apply click-through (Tk may have recreated the window)
        and keep the banner on top."""
        if self.positioning or not self.shown or self._ghosted:
            return
        self.app.clickthrough.set(self.tw, True)
        if self._back_on:
            self.app.clickthrough.set(self.back, True)
        self._stack()

    MIN_SIZE, MAX_SIZE = 16, 160

    def bump_size(self, delta):
        """Change the alert text size (only while positioning the banner)."""
        if not self.positioning:
            return
        size = int(self.app.settings.num("alert_font_size", 48)) + delta
        size = max(self.MIN_SIZE, min(self.MAX_SIZE, size))
        self.app.settings["alert_font_size"] = size
        self.font.configure(size=size)
        self.draw()

    # -- positioning mode (normal window you can move and resize)
    def toggle_positioning(self):
        if self.positioning:
            self.stop_positioning()
        else:
            self.positioning = True
            self._ghosted = False
            for w in (self.tw, self.back, self.win):
                w.withdraw()
            self._back_on = False
            self._set_borderless(False)
            self.app.clickthrough.set(self.win, False)  # must be clickable to drag it
            self.win.deiconify()
            self.shown = True
            self.draw()
            self.c.focus_set()  # so + / - work right away
        self.app.on_window_toggled()

    def stop_positioning(self):
        if not self.positioning:
            return
        self.positioning = False
        self.remember()
        self.app.settings.save()
        self._hide()
        self.app.on_window_toggled()

    def remember(self):
        try:
            wins = dict(self.app.settings["windows"] or {})
            wins[self.name] = {"geometry": self.win.geometry(), "visible": False}
            self.app.settings["windows"] = wins
        except tk.TclError:
            pass

    def _press(self, e):
        self._drag_from = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())

    def _drag(self, e):
        if not self._drag_from:
            return
        x0, y0, wx, wy = self._drag_from
        self.win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    # -- alerts
    def push(self, text, now, key=""):
        """Show a line. A line with a key replaces the earlier line with the same key
        (a countdown shows one line that changes, not a stack of them)."""
        secs = max(1.0, self.app.settings.num("alert_seconds", 5))
        keys = getattr(self, "_keys", {})
        self.alerts = [(t, x) for t, x in self.alerts
                       if t != text and x > now and not (key and keys.get(t) == key)]
        self._keys = keys = {t: keys[t] for t, _ in self.alerts if t in keys}
        if key:
            keys[text] = key
        self.alerts.insert(0, (text, now + secs))
        del self.alerts[3:]
        self._show()
        self.draw()

    def refresh(self, now):
        if self.positioning:
            return
        before = len(self.alerts)
        self.alerts = [(t, x) for t, x in self.alerts if x > now]
        if not self.alerts:
            if self.shown:
                self._hide()
        elif len(self.alerts) != before:
            self.draw()

    def _fit(self, lines, lh, w, h):
        """Grow the positioning frame (never shrink it) if the text no longer fits."""
        try:
            widest = max(self.font.measure(t) for t in lines) + 40
            need_w = min(max(w, widest), max(400, self.app.root.winfo_screenwidth() or 1920))
            need_h = lh * len(lines) + (50 if self.positioning else 20)
            if need_w > w + 2 or need_h > h + 2:
                self.win.geometry(f"{int(max(w, need_w))}x{int(max(h, need_h))}")
        except (tk.TclError, TypeError, ValueError):
            pass

    def draw(self):
        if self.positioning:
            self._draw_sample()
        else:
            self._draw_alert()

    def _text_lines(self, c, lines, w, top, lh, color):
        for i, text in enumerate(lines):
            y = top + lh * i + lh / 2
            for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (3, 3)):  # dark outline
                c.create_text(w / 2 + dx, y + dy, text=text, fill="#000000", font=self.font,
                              width=w - 20, justify="center")
            c.create_text(w / 2, y, text=text, fill=color, font=self.font,
                          width=w - 20, justify="center")

    def _draw_sample(self):
        """While positioning: the frame you drag, with a sample at full strength."""
        c = self.c
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 80)
        size = int(self.app.settings.num("alert_font_size", 48))
        c.create_text(w / 2, h - 14, fill="#c9d1d9", font=self.app.fonts["small"],
                      text=f"Text size {size}: scroll or press + / - to change.  Drag or "
                           "resize to place, then click 'Lock alerts' in the main window.  "
                           "The banner is centred on this frame.",
                      width=w - 20, justify="center")
        lh = self.font.metrics("linespace")
        self._fit([self.SAMPLE], lh, w, h)
        top = max(0, (h - lh) / 2)
        self._text_lines(c, [self.SAMPLE], w, top, lh, self.app.settings["alert_color"] or "#FFD400")

    def _frame_center(self):
        """Centre of the positioning frame, from its saved geometry (works while it is hidden)."""
        try:
            size, _, pos = self.win.geometry().partition("+")
            fw, fh = (int(v) for v in size.split("x"))
            x, _, y = pos.partition("+")
            return int(x) + fw / 2, int(y) + fh / 2
        except (tk.TclError, ValueError):
            return 450.0, 260.0

    def _draw_alert(self):
        """Draw the alert lines in the solid text window, sized to the text and
        centred on the frame, with the translucent panel behind it."""
        c = self.tc
        c.delete("all")
        lines = [t for t, _ in self.alerts]
        if not lines:
            return
        lh = self.font.metrics("linespace")
        widest = max(self.font.measure(t) for t in lines)
        sw = max(400, self.app.root.winfo_screenwidth() or 1920)
        w = int(min(widest + 2 * self.PAD_X + 8, sw))
        h = int(lh * len(lines) + 2 * self.PAD_Y + 8)
        cx, cy = self._frame_center()
        x, y = int(cx - w / 2), int(cy - h / 2)
        self.tw.geometry(f"{w}x{h}+{x}+{y}")
        self._text_lines(c, lines, w, self.PAD_Y + 4, lh, self.app.settings["alert_color"] or "#FFD400")
        # the translucent panel: a margin around the text, at the slider's opacity
        a = self.back_alpha()
        if a <= 0.004:
            if self._back_on:
                self.back.withdraw()
                self._back_on = False
        else:
            hx = self.HALO
            self.back.geometry(f"{w + 2 * hx}x{h + 2 * hx}+{x - hx}+{y - hx}")
            try:
                self.back.attributes("-alpha", a)
            except tk.TclError:
                pass
            self.app.decorations.set_opacity(self.back, a)
            if not self._back_on:
                self.back.deiconify()
                self._back_on = True
            self.app.clickthrough.set(self.back, True)
        self.app.clickthrough.set(self.tw, True)
        self._stack()
        self._shape_text(w, h)

    def _shape_text(self, w, h):
        """Cut the text window down to the letters and their outline, so the only thing
        at full strength is the text; the dark rectangle behind them belongs to the
        translucent panel (and goes away at 0%)."""
        ct = self.app.clickthrough
        if not ct.shape_available:
            return
        key = (tuple(t for t, _ in self.alerts), w, h, self.font.cget("size"),
               self.app.settings["alert_color"])
        cached = getattr(self, "_shape_cache", None)
        if cached and cached[0] == key and cached[1]:   # same picture as before: reuse its shape
            ct.set_shape(self.tw, cached[1])
            return
        # The pixels must be read from the whole, unshaped window (X throws away what is
        # drawn outside a window's shape), so show it in full but fully see-through
        # while measuring, then cut it to the letters and make it solid.
        ct.set_shape(self.tw, None)
        self._tw_alpha(0.0)
        self._shape_token = token = getattr(self, "_shape_token", 0) + 1

        def apply():
            if token != self._shape_token:
                return                                  # a newer measurement is under way
            if not self.alerts:
                self._tw_alpha(1.0)
                return
            try:
                self.tw.update_idletasks()
                rects = ct.glyph_rects(self.tw, w, h)
                if rects:
                    ct.set_shape(self.tw, rects)
                self._shape_cache = (key, rects if rects else None)
            except tk.TclError:
                pass
            finally:
                self._tw_alpha(1.0)
        self.tw.after(110, apply)

    def _tw_alpha(self, alpha):
        try:
            self.tw.attributes("-alpha", alpha)
        except tk.TclError:
            pass
        self.app.decorations.set_opacity(self.tw, alpha)
