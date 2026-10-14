"""Find out what is really happening to the overlay windows on this machine.

Reads the live X11 state with ctypes (no extra packages): whether each overlay
really is an unmanaged window, what its input and visible shapes are, what
opacity the compositor was told, whether a compositing manager is running at
all, what sits above each overlay, and, with the pointer probe, which window
would actually receive a click at the mouse position.
"""
from .. import VERSION_LABEL
import logging
import os
import platform
import sys
import time
import tkinter as tk
from tkinter import ttk

from .base import P, OverlayWindow
from . import xguard
from .xinspect import SHAPE_BOUNDING, SHAPE_INPUT, XInspector, conclude, on_wayland

log = logging.getLogger(__name__)

def _tk_ids(widget, out=None):
    """X window ids of a Tk window and everything inside it."""
    out = set() if out is None else out
    try:
        out.add(int(widget.winfo_id()))
        for ch in widget.winfo_children():
            _tk_ids(ch, out)
    except (tk.TclError, ValueError, TypeError):
        pass
    return out


def _fmt_rects(r, size=None, limit=4):
    if not r:
        return "EMPTY (nothing)"
    if size and len(r) == 1 and tuple(r[0]) == (0, 0, size[0], size[1]):
        return "whole window (normal)"
    s = ", ".join(f"{w}x{h}+{x}+{y}" for x, y, w, h in r[:limit])
    return f"{len(r)} rect(s): {s}" + (" ..." if len(r) > limit else "")


def session_label():
    """e.g. 'Wayland (apps run through XWayland), KDE'."""
    desk = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "unknown desktop"
    if on_wayland():
        kind = "Wayland (this app runs through XWayland)"
    elif os.environ.get("DISPLAY"):
        kind = "X11"
    else:
        kind = "unknown session"
    return f"{kind}, {desk}"


def environment(app):
    try:
        tkv = app.root.tk.call("info", "patchlevel")
    except Exception:
        tkv = "?"
    lines = [
        f"Everlast {VERSION_LABEL}, Python {platform.python_version()}, Tk {tkv}",
        f"Session: type={os.environ.get('XDG_SESSION_TYPE', '?')}  "
        f"wayland-display={os.environ.get('WAYLAND_DISPLAY', '-')}  "
        f"display={os.environ.get('DISPLAY', '-')}",
        f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', '?')} / "
        f"{os.environ.get('DESKTOP_SESSION', '?')}",
    ]
    return lines


def build_report(app, xi=None):
    """A plain-text report of every overlay window's real state."""
    xi = xi or XInspector()
    lines = ["== Overlay diagnostics =="] + environment(app)
    if sys.platform.startswith("win") or not xi.available:
        lines.append(f"X11 inspection unavailable: {xi.reason or 'not X11'}")
        return "\n".join(lines)
    wm = xi.window_manager() or "(none found)"
    lines.append(f"Window manager: {wm}")
    if on_wayland():
        lines.append("Wayland session: this app runs through XWayland. The desktop does the "
                     "compositing, and whether it honours click-through regions on XWayland "
                     "windows is up to it. If the pointer probe says clicks pass through but "
                     "real clicks do not, that is the cause; logging in to an X11 session "
                     "(chosen on the login screen) avoids it.")
    else:
        comp = xi.compositor_running()
        lines.append("Compositing manager: " + ("running" if comp else
                     "NOT RUNNING - window opacity cannot work without one; windows set to "
                     "0% will simply look opaque"))
    hints = xi.compositing_hints()
    if hints:
        lines.append("Windows that are fullscreen or ask the compositor to step aside:")
        for name, bypass, full in hints:
            bits = []
            if bypass == 1:
                bits.append("asks to BYPASS compositing (_NET_WM_BYPASS_COMPOSITOR = 1)")
            if full:
                bits.append("fullscreen")
            lines.append(f"  '{name}': " + ", ".join(bits))
        if any(b == 1 for _, b, _ in hints):
            lines.append("  While a window like this is up, your desktop may stop compositing. Then the "
                         "overlays' see-through panels and shapes can misbehave or show stale graphics. "
                         "Try the game in a window or borderless-windowed, or turn off your compositor's "
                         "option that lets applications block compositing (KDE: System Settings, "
                         "Display, Compositor).")
    else:
        lines.append("No window is fullscreen or asking to bypass compositing right now. (Windows that "
                     "set _NET_WM_BYPASS_COMPOSITOR to 2, as Chrome and Discord do, are asking the "
                     "opposite: that compositing stay ON. They are fine.)")
    ct = app.clickthrough
    lines.append(f"Click-through: {'available' if ct.available else 'unavailable: ' + ct.reason}"
                 f"; shapes: {'yes' if ct.shape_available else 'no'}; "
                 f"overlays locked: {bool(app.settings['overlays_locked'])}")
    try:
        ptr = app.root.winfo_pointerxy()
    except Exception:
        ptr = "?"
    if xguard.count:
        last = ", ".join(f"code {c} on request {r} (0x{w:x})" for c, r, w in xguard.recent[-3:])
        lines.append(f"X errors absorbed so far: {xguard.count} (most recent: {last}). They are expected "
                     "now and then, since Tk replaces windows; a steady stream would not be.")
    else:
        lines.append("X errors absorbed so far: 0")
    lines.append(f"Hover-hide: {'on' if app.ghost_enabled() else 'off'} "
                 f"(setting {app.settings['ghost_under_mouse']}); pointer as Tk sees it: {ptr}")
    lines.append("")
    items = [(w.name, w.win, w) for w in app.windows.values()]
    items.append(("alerts", app.alert_win.win, None))
    for w in app.windows.values():
        if getattr(w, "_backdrop", None) is not None:
            items.append((w.name + " backdrop", w._backdrop.win, None))
    for name, win, ow in items:
        try:
            outer, inner = ct.window_id(win), ct.inner_id(win)
            if not outer:
                lines.append(f"[{name}] not on screen (hidden)")
                continue
            a = xi.attrs(outer)
            parent, _ = xi.tree(outer)
            state = ""
            if ow is not None:
                state = (f" locked={ow.locked} passthrough={ow.passthrough} "
                         f"solid={ow.use_solid} unmanaged={ow.unmanaged} hidden-by-hover={ow._ghosted}")
            lines.append(f"[{name}]{state}")
            par = "the root: it IS the top-level window" if parent == xi.root else \
                f"0x{parent:x}: NOT the top-level window, something is above it"
            stack = [w for w, _dx, _dy in ct.chain(win)] or [inner]
            lines.append(f"  window stack (Tk's window first): " + " -> ".join(
                f"0x{w:x}" + (" (top-level)" if w == outer else "") for w in stack)
                + f"; the top-level's parent is {par}")
            if a:
                lines.append(f"  top-level: override-redirect={bool(a.override_redirect)}; "
                             f"{a.width}x{a.height}")
            for w in reversed(stack):
                wa = xi.attrs(w)
                size = (wa.width, wa.height) if wa else None
                label = "top-level" if w == outer else ("Tk window" if w == inner else "middle")
                lines.append(f"  {label:9} 0x{w:x}: click region {_fmt_rects(xi.rects(w, SHAPE_INPUT), size)}"
                             f" | visible region {_fmt_rects(xi.rects(w, SHAPE_BOUNDING), size, 2)}")
            op = xi.opacity(outer)
            lines.append("  opacity hint on window: " + ("none (100%)" if op is None else f"{op * 100:.0f}%"))
            ab = xi.above(outer)
            if ab:
                lines.append("  windows above it: " + "; ".join(f"{n} ({w}x{h})" for n, w, h in ab))
        except Exception as e:
            lines.append(f"[{name}] could not be inspected: {e}")
    lines += ["", "What a click would hit: use 'Probe pointer' below, with the mouse held "
                  "over a locked overlay."]
    return "\n".join(lines)


def probe_pointer(app, xi=None):
    """Which window would receive a click at the current mouse position?"""
    xi = xi or XInspector()
    if not xi.available:
        return f"Pointer probe unavailable: {xi.reason}"
    ours = set()
    for w in list(app.windows.values()) + [app.alert_win]:
        ours |= _tk_ids(w.win)
        ours.add(app.clickthrough.window_id(w.win))
        bd = getattr(w, "_backdrop", None)
        if bd is not None:
            ours |= _tk_ids(bd.win)
            ours.add(app.clickthrough.window_id(bd.win))
    ours |= _tk_ids(app.root)
    ours.add(app.clickthrough.window_id(app.root))
    px, py = xi.pointer()
    chain = xi.chain_at(px, py)
    if not chain:
        return f"Pointer at {px},{py}: over the bare desktop (root window). Clicks reach nothing."
    top = chain[0]
    nm = xi.toplevel_name(top) or f"window 0x{top:x}"
    mine = [c for c in chain if c in ours]
    title = ""
    for w in list(app.windows.values()) + [app.alert_win]:
        try:
            if top in (app.clickthrough.window_id(w.win),):
                title = getattr(w, "title", "Alerts")
        except Exception:
            pass
    if mine:
        return (f"Pointer at {px},{py}: a click would land on an Everlast window "
                f"({title or nm}). Click-through is NOT working for this spot.")
    return (f"Pointer at {px},{py}: a click would pass through to '{nm}' "
            f"(0x{top:x}). Click-through is working for this spot.")


class DiagnosticsDialog:
    """Report viewer with Refresh / Probe pointer / Copy."""

    def __init__(self, app):
        self.app = app
        self.xi = XInspector()
        self.top = tk.Toplevel(app.root)
        self.top.title("Overlay diagnostics - Everlast")
        self.top.configure(bg=P["bg"])
        self.top.geometry("760x560")
        bar = ttk.Frame(self.top, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Probe pointer in 5 s", command=self.probe).pack(side="left", padx=6)
        ttk.Button(bar, text="Click-through test", command=lambda: ClickTestDialog(app)).pack(
            side="left", padx=6)
        ttk.Button(bar, text="Copy all", command=self.copy).pack(side="left")
        self.note = ttk.Label(bar, style="Dim.TLabel")
        self.note.pack(side="left", padx=10)
        self.text = tk.Text(self.top, wrap="none", bg=P["panel"], fg=P["text"], bd=0,
                            highlightthickness=0, padx=8, pady=6, font="TkFixedFont")
        self.text.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.refresh()

    def _show(self, text):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", text)
        self.text.configure(state="disabled")
        self._last = text
        try:
            path = os.path.join(os.path.expanduser("~"), ".cache", "eqtool-native", "diagnostics.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass

    def refresh(self):
        try:
            self._show(build_report(self.app, self.xi))
        except Exception as e:
            log.exception("diagnostics failed")
            self._show(f"Diagnostics failed: {e}")

    def probe(self):
        self.countdown = 5
        self._tick()

    def _tick(self):
        if self.countdown > 0:
            self.note.configure(text=f"Hold the mouse over a locked overlay... {self.countdown}")
            self.countdown -= 1
            self.top.after(1000, self._tick)
            return
        self.note.configure(text="")
        try:
            result = probe_pointer(self.app, self.xi)
        except Exception as e:
            log.exception("probe failed")
            result = f"Probe failed: {e}"
        self._show(self._last.split("\n\nPROBE:")[0] + "\n\nPROBE: " + result)

    def copy(self):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(self._last)
        self.note.configure(text="Copied.")


class ClickTestDialog:
    """A self-contained click-through test on the real desktop path.

    Buttons A and B each have a red see-through overlay over them, set up the
    way the locked timer windows are (A: empty click region; B: also cut to a
    shape, like the solid timers). Button C is a control with nothing over it.
    Every click that reaches this window or an overlay is counted and logged,
    and the real positions of buttons and overlays are shown, so whichever way
    it goes the result says what happened.
    """

    def __init__(self, app):
        self.app = app
        self.through = {"A": 0, "B": 0, "D": 0, "C": 0}
        self.blocked = {"A": 0, "B": 0, "D": 0}
        self.margin = {"A": 0, "B": 0, "D": 0}      # clicks on a button's border, outside its red square
        self.background = 0
        self.cover_rect = {}                # key -> (x, y, w, h) on screen, from the last placement
        self.base = tk.Toplevel(app.root)
        self.base.title("Click-through test - Everlast")
        self.base.configure(bg=P["bg"])
        self.base.geometry("860x560+160+120")
        self.base.protocol("WM_DELETE_WINDOW", self.close)
        ttk.Label(self.base, text="Your session: " + session_label(), style="Head.TLabel").pack(
            pady=(8, 0))
        ttk.Label(self.base, text="1) Click the grey Button C.   2) Click INSIDE red squares A and B "
                                  "(not their grey border).   3) For D: move the mouse onto the red "
                                  "square, wait for it to vanish, then click where it was.", wraplength=820).pack(pady=(10, 4))
        row = ttk.Frame(self.base)
        row.pack(fill="x", padx=12)
        self.buttons, self.covers = {}, {}
        for key, text in (("A", "A: click region only"), ("B", "B: shaped like the timers"),
                          ("D", "D: hides when the mouse is over it"), ("C", "Control: nothing over it")):
            col = ttk.Frame(row)
            col.pack(side="left", fill="both", expand=True, padx=6)
            ttk.Label(col, text=text, style="Head.TLabel").pack()
            b = tk.Button(col, text=f"Button {key}", height=5, wraplength=160,
                          command=lambda k=key: self._through(k))
            b.pack(fill="both", expand=True, pady=4)
            self.buttons[key] = b
        self.summary_var = tk.StringVar(value=conclude(self.through, self.blocked, 0, self.margin))
        ttk.Label(self.base, textvariable=self.summary_var, wraplength=820, justify="left",
                  font=app.fonts["bold"]).pack(fill="x", padx=14, pady=(8, 4))
        self.pointer_var = tk.StringVar(value="")
        ttk.Label(self.base, textvariable=self.pointer_var, style="Dim.TLabel", wraplength=820,
                  justify="left", font=app.fonts["small"]).pack(fill="x", padx=14)
        self.geom_var = tk.StringVar(value="")
        ttk.Label(self.base, textvariable=self.geom_var, style="Dim.TLabel", wraplength=820,
                  justify="left", font=app.fonts["small"]).pack(fill="x", padx=14)
        self.log = tk.Text(self.base, height=9, wrap="word", bg=P["panel"], fg=P["text"], bd=0,
                           highlightthickness=0, padx=6, pady=4, font="TkFixedFont")
        self.log.pack(fill="both", expand=True, padx=12, pady=6)
        bar = ttk.Frame(self.base)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(bar, text="Copy result", command=self.copy).pack(side="left")
        ttk.Button(bar, text="Close", command=self.close).pack(side="right")
        self.base.bind("<Button-1>", self._any_click, add="+")   # every click that reaches this window
        self.job = self.ghost_job = None
        self._d_ghost = False
        self._last_ptr, self._ptr_changed = None, time.monotonic()
        for key in ("A", "B", "D"):
            c = tk.Toplevel(app.root)
            try:
                c.overrideredirect(True)
            except tk.TclError:
                pass
            c.configure(bg="#c0392b")
            lab = tk.Label(c, text=("D: point here, then click\nwhere it was" if key == "D"
                                    else f"{key}: click me"), bg="#c0392b", fg="white")
            lab.pack(fill="both", expand=True)
            for w in (c, lab):
                w.bind("<Button-1>", lambda e, k=key: self._blocked(k))
            try:
                c.attributes("-alpha", 0.6)
            except tk.TclError:
                pass
            self.covers[key] = c
        self._write("Started. Overlays are re-applied once a second, like the real ones.")
        self.base.after(250, self._place)
        self.ghost_job = self.base.after(300, self._ghost_tick)

    # -- helpers
    def _write(self, text):
        try:
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except tk.TclError:
            pass
        self._text = getattr(self, "_text", "") + text + "\n"

    def _refresh_summary(self):
        self.summary_var.set(conclude(self.through, self.blocked, self.background, self.margin))

    def _rect(self, w):
        try:
            return f"{w.winfo_width()}x{w.winfo_height()}+{w.winfo_rootx()}+{w.winfo_rooty()}"
        except tk.TclError:
            return "?"

    def _place(self):
        try:
            self.base.update_idletasks()
            ct = self.app.clickthrough
            info = [f"test window {self._rect(self.base)}"]
            for key, c in self.covers.items():
                if key == "D" and self._d_ghost:
                    continue
                b = self.buttons[key]
                x, y, w, h = b.winfo_rootx(), b.winfo_rooty(), b.winfo_width(), b.winfo_height()
                cw, ch = max(20, w - 30), max(20, h - 30)
                c.geometry(f"{cw}x{ch}+{x + 15}+{y + 15}")
                self.cover_rect[key] = (x + 15, y + 15, cw, ch)
                if key == "B":   # cut to two stripes, like the timer bars
                    stripe = max(10, ch // 3)
                    ct.set_shape(c, [(0, 0, cw, stripe), (0, stripe * 2, cw, stripe)])
                ct.set(c, True)
                c.lift()
                info.append(f"{key}: button {self._rect(b)} / overlay {self._rect(c)}")
            self.geom_var.set("Positions as Tk sees them: " + "; ".join(info))
        except tk.TclError:
            return
        self.job = self.base.after(1000, self._place)

    def _ghost_tick(self):
        """Test D: the same hide-while-hovered logic the locked overlays use, run
        fast, plus a readout of the pointer position the desktop gives us."""
        try:
            px, py = self.base.winfo_pointerxy()
            now = time.monotonic()
            if (px, py) != self._last_ptr:
                self._last_ptr, self._ptr_changed = (px, py), now
            idle = now - self._ptr_changed
            self.pointer_var.set(
                f"Pointer as Tk sees it: ({px}, {py})" +
                ("" if idle < 3 else f"  - unchanged for {int(idle)} s (normal if you are not "
                 "moving the mouse; if you ARE moving it, the desktop is not reporting it to "
                 "this app there)"))
            rect, c = self.cover_rect.get("D"), self.covers["D"]
            if rect:
                inside = OverlayWindow._inside
                if not self._d_ghost and inside(rect, px, py):
                    self._d_ghost = True
                    c.withdraw()
                    self._write(f"overlay D hid itself: pointer at ({px},{py}) is over it")
                elif self._d_ghost and not inside(rect, px, py, OverlayWindow.GHOST_MARGIN):
                    self._d_ghost = False
                    c.deiconify()
                    c.lift()
                    self._write("overlay D came back: the pointer left")
        except tk.TclError:
            return
        self.ghost_job = self.base.after(50, self._ghost_tick)

    # -- events
    def _any_click(self, e):
        widget = e.widget
        key = next((k for k, b in self.buttons.items() if b is widget), None)
        where = f"({e.x_root},{e.y_root})"
        if key == "C":
            self.through["C"] += 1
            self._write(f"click at {where} reached control button C")
        elif key:
            x, y, w, h = self.cover_rect.get(key, (0, 0, 0, 0))
            if x <= e.x_root < x + w and y <= e.y_root < y + h:
                self.through[key] += 1
                self._write(f"click at {where} went through red square {key} and reached its button")
            else:
                self.margin[key] += 1
                self._write(f"click at {where} hit button {key}'s grey border, OUTSIDE the red "
                            "square (not a click-through test)")
        else:
            self.background += 1
            self._write(f"click at {where} reached the test window's background")
        self._refresh_summary()
        self.base.after(60, self._place)     # a click may have raised this window above the overlays

    def _through(self, k):
        """Button command: only updates the label; counting is done in _any_click."""
        self.buttons[k].configure(text=f"Button {k}: pressed")

    def _blocked(self, k):
        self.blocked[k] += 1
        self._write(f"overlay {k} CAUGHT a click (it did not pass through)")
        self._refresh_summary()

    def copy(self):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append("Session: " + session_label() + "\n" + self.summary_var.get()
                                       + "\n" + self.geom_var.get() + "\n" + getattr(self, "_text", ""))

    def close(self):
        for job in (self.job, self.ghost_job):
            if job:
                try:
                    self.base.after_cancel(job)
                except tk.TclError:
                    pass
        for c in self.covers.values():
            try:
                c.destroy()
            except tk.TclError:
                pass
        try:
            self.base.destroy()
        except tk.TclError:
            pass
