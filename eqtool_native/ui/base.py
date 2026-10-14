"""Theme and the base class for the floating overlay windows."""
import logging
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .xinspect import on_wayland

log = logging.getLogger(__name__)

# Deep slate with brass trim, echoing the classic EQ window chrome.
P = {
    "bg": "#161d28", "panel": "#212a38", "line": "#2f3b4e", "text": "#e9e0c9",
    "dim": "#8f99a8", "accent": "#c9a35a",
    "buff": "#3c77a8", "debuff": "#a8492f", "recast": "#566172", "spawn": "#6f58a6",
    "custom": "#467f58", "boat": "#2f7f86", "ringwar": "#9a5a2f", "ch": "#7a5aa6", "up": "#d6ad45", "you": "#b8923f", "other": "#3c6f96",
}
KIND_COLOR = {"buff": P["buff"], "debuff": P["debuff"], "recast": P["recast"],
              "spawn": P["spawn"], "custom": P["custom"], "boat": P["boat"],
              "ringwar": P["ringwar"], "ch": P["ch"]}

_FAMILIES = ("Inter", "Cantarell", "Noto Sans", "DejaVu Sans", "Liberation Sans", "Helvetica")


def make_fonts(root, size):
    try:
        avail = set(tkfont.families(root))
    except tk.TclError:
        avail = set()
    fam = next((f for f in _FAMILIES if f in avail), "TkDefaultFont")
    return {
        "normal": tkfont.Font(root, family=fam, size=size),
        "bold": tkfont.Font(root, family=fam, size=size, weight="bold"),
        "small": tkfont.Font(root, family=fam, size=max(7, size - 2)),
    }


def setup_style(root):
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", background=P["bg"], foreground=P["text"], fieldbackground=P["panel"],
                 bordercolor=P["line"], lightcolor=P["line"], darkcolor=P["line"])
    st.configure("TButton", background=P["panel"], foreground=P["text"], padding=(8, 3))
    st.map("TButton", background=[("active", P["line"])])
    st.configure("Tool.TButton", padding=(6, 1))
    st.configure("TCheckbutton", background=P["bg"], foreground=P["text"])
    st.map("TCheckbutton", background=[("active", P["bg"])])
    st.configure("TLabel", background=P["bg"], foreground=P["text"])
    st.configure("Dim.TLabel", foreground=P["dim"])
    st.configure("Head.TLabel", foreground=P["accent"])
    st.configure("TFrame", background=P["bg"])
    st.configure("TEntry", fieldbackground=P["panel"], foreground=P["text"],
                 insertcolor=P["text"])
    st.configure("TCombobox", fieldbackground=P["panel"], foreground=P["text"],
                 background=P["panel"], arrowcolor=P["text"])
    st.map("TCombobox", fieldbackground=[("readonly", P["panel"])],
           foreground=[("readonly", P["text"])])
    st.configure("TSpinbox", fieldbackground=P["panel"], foreground=P["text"],
                 arrowcolor=P["text"])
    # Tabs stay dark like the rest of the window; the chosen tab is lighter with brass text.
    st.configure("TNotebook", background=P["bg"], bordercolor=P["line"], lightcolor=P["bg"],
                 darkcolor=P["bg"], tabmargins=(2, 4, 2, 0))
    st.configure("TNotebook.Tab", background=P["panel"], foreground=P["text"],
                 bordercolor=P["line"], lightcolor=P["panel"], darkcolor=P["panel"],
                 padding=(12, 4))
    st.map("TNotebook.Tab",
           background=[("selected", P["line"]), ("active", P["line"])],
           foreground=[("selected", P["accent"]), ("active", P["accent"])],
           lightcolor=[("selected", P["line"])], darkcolor=[("selected", P["line"])])
    st.configure("TMenubutton", background=P["panel"], foreground=P["text"], padding=(8, 3),
                 arrowcolor=P["text"], bordercolor=P["line"])
    st.map("TMenubutton", background=[("active", P["line"])])
    st.configure("Exit.TButton", background="#5a2a2a", foreground=P["text"], padding=(8, 3))
    st.map("Exit.TButton", background=[("active", "#7a3434")])
    st.configure("Treeview", background=P["panel"], fieldbackground=P["panel"],
                 foreground=P["text"], bordercolor=P["line"], rowheight=22)
    st.configure("Treeview.Heading", background=P["line"], foreground=P["accent"],
                 relief="flat")
    st.map("Treeview", background=[("selected", P["line"])],
           foreground=[("selected", P["accent"])])
    st.map("Treeview.Heading", background=[("active", P["line"])])
    st.configure("TScale", background=P["bg"], troughcolor=P["panel"])
    root.option_add("*Menu.background", P["panel"])
    root.option_add("*Menu.foreground", P["text"])
    root.option_add("*Menu.activeBackground", P["line"])
    root.option_add("*Menu.activeForeground", P["accent"])
    root.option_add("*Menu.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.background", P["panel"])
    root.option_add("*TCombobox*Listbox.foreground", P["text"])
    return st


def fmt_secs(s):
    s = int(max(0, s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class Backdrop:
    """A plain translucent panel kept just behind a "solid" overlay window.

    Tk can only make a whole window see-through, which would fade the timer
    bars and text along with the background. So a solid window is cut (with the
    X Shape extension) down to just its bars and text and drawn fully opaque,
    and this panel behind it supplies the adjustable see-through background.
    It never takes a click."""
    PAD = 4

    def __init__(self, owner):
        self.owner = owner
        self.win = tk.Toplevel(owner.app.root)
        self.win.withdraw()
        try:
            self.win.overrideredirect(True)
        except tk.TclError:
            pass
        self.win.configure(bg=P["bg"])
        # Painted by a widget, not left to the window's background: a bare window's
        # background is filled by the X server, and on some desktops that fill does not
        # happen before the window is shown, so the panel displays whatever was last
        # in video memory (pieces of other windows). A canvas paints every pixel itself.
        self.fill = tk.Canvas(self.win, bg=P["bg"], highlightthickness=0, bd=0)
        self.fill.pack(fill="both", expand=True)
        self.alpha = 1.0
        self.shown = False
        self._geom = None

    def set_alpha(self, alpha):
        self.alpha = max(0.0, min(1.0, float(alpha)))
        try:
            self.win.attributes("-alpha", self.alpha)
        except tk.TclError:
            pass
        self.owner.app.decorations.set_opacity(self.win, self.alpha)

    def place(self, box, force=False):
        """box = (x, y, w, h) in the owner window's coordinates, or None."""
        o = self.owner
        try:
            on_screen = o.is_visible() and bool(o.win.winfo_ismapped())
        except tk.TclError:
            on_screen = False
        if box is None or self.alpha <= 0.004 or not on_screen:
            self.hide()
            return
        try:
            rx, ry = o.win.winfo_rootx(), o.win.winfo_rooty()
        except tk.TclError:
            return
        x, y, w, h = box
        geom = f"{max(1, int(w))}x{max(1, int(h))}+{int(rx + x)}+{int(ry + y)}"
        changed = geom != self._geom
        if changed:
            self.win.geometry(geom)
            self._geom = geom
        if not self.shown:
            self.win.deiconify()
            self.shown = True
            changed = True
        if changed or force:
            self.set_alpha(self.alpha)            # re-applied after every (re)map
            o.app.clickthrough.set(self.win, True)
            self.stack()

    def stack(self):
        """Just below its window, without raising anything."""
        if self.shown:
            try:
                self.win.lower(self.owner.win)
            except tk.TclError:
                pass

    def hide(self):
        if self.shown or self._geom:
            try:
                self.win.withdraw()
            except tk.TclError:
                pass
        self.shown = False
        self._geom = None


class OverlayWindow:
    name = "overlay"
    title = "Overlay"
    default_geometry = "300x300+100+100"
    solid = False            # True: bars/text stay opaque, only the background fades
    always_top = False       # True: kept above everything whatever the settings say

    def __init__(self, app):
        self.app = app
        self._backdrop = None
        self._shape_key, self._shape_wid = None, 0
        self._ghosted, self._ghost_rect, self._ghost_pos = False, None, None
        self._auto_hidden = False      # unmapped because there is nothing to show
        self._ghost_since, self._ghost_block = 0.0, None
        self.win = tk.Toplevel(app.root)
        self.win.title(f"{self.title} - Everlast")
        self.win.configure(bg=P["bg"])
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win.minsize(160, 80)
        # Cover the whole window with a widget that paints it (see Backdrop): the margins
        # around the other widgets would otherwise be left to the X server's own fill.
        self._paint = tk.Frame(self.win, bg=P["bg"], bd=0, highlightthickness=0)
        self._paint.place(x=0, y=0, relwidth=1, relheight=1)
        self._paint.lower()
        conf = (app.settings["windows"] or {}).get(self.name, {})
        try:
            self.win.geometry(conf.get("geometry") or self.default_geometry)
        except tk.TclError:
            self.win.geometry(self.default_geometry)
        self.locked = bool(app.settings["overlays_locked"])
        self.borderless = self.unmanaged = False
        # Ctrl + mouse wheel anywhere in the window: this window's opacity.
        self.win.bind("<Control-Button-4>", lambda e: self._wheel_opacity(e, 0.05))
        self.win.bind("<Control-Button-5>", lambda e: self._wheel_opacity(e, 0.05))
        self.win.bind("<Control-MouseWheel>", lambda e: self._wheel_opacity(e, 0.05))
        self.build()
        # Without a title bar there has to be something to grab: a strip that is
        # shown only while the window can be moved (unlocked and borderless).
        self._handle = tk.Label(self.win, text=f"\u2725  {self.title}  \u2014  drag here to move",
                                bg=P["accent"], fg=P["bg"], font=app.fonts["small"], pady=2,
                                cursor="fleur")
        self._handle_shown = False
        self.draggable(self._handle)
        for ev in ("<Configure>", "<Map>"):
            self.win.bind(ev, self._on_geometry, add="+")
        self.win.bind("<Unmap>", self._on_unmap, add="+")
        self.apply_prefs()
        if not conf.get("visible", True):
            self.win.withdraw()
        if self.locked or app.settings["hide_borders"] or self.use_solid:
            self.apply_chrome()

    # subclasses implement
    def build(self):
        pass

    def refresh(self, now):
        pass

    def apply_prefs(self):
        try:
            self.win.attributes("-topmost", bool(self.locked or self.always_top
                                                 or self.app.settings["always_on_top"]))
        except tk.TclError:
            pass
        self.set_opacity(self.opacity(), save=False)

    # -- opacity (needs a compositing desktop; ignored otherwise)
    MIN_OPACITY = 0.0

    def opacity(self):
        """This window's own opacity if you set one, else the global one."""
        own = (self.app.settings["window_opacity"] or {}).get(self.name)
        try:
            val = float(own) if own is not None else self.app.settings.num("opacity", 0.92)
        except (TypeError, ValueError):
            val = 0.92
        return max(self.MIN_OPACITY, min(1.0, val))

    @property
    def use_solid(self):
        """Bars and text opaque over an adjustable see-through background."""
        return (self.solid and bool(self.app.settings["solid_timers"])
                and self.app.clickthrough.shape_available)

    def backdrop(self):
        if self._backdrop is None:
            self._backdrop = Backdrop(self)
        return self._backdrop

    def _set_window_alpha(self, value):
        try:
            self.win.attributes("-alpha", value)
        except tk.TclError:
            pass
        # Also set the compositor hint directly so it survives lock/borderless.
        self.app.decorations.set_opacity(self.win, value)

    def set_opacity(self, value, save=True):
        value = max(self.MIN_OPACITY, min(1.0, float(value)))
        if self.use_solid:
            self._set_window_alpha(1.0)          # the timers themselves: always 100%
            self.backdrop().set_alpha(value)     # the slider is the background
            self.sync_solid(force=True)
        else:
            self._set_window_alpha(value)
            if self._backdrop is not None:
                self._backdrop.hide()
        if save:
            per = dict(self.app.settings["window_opacity"] or {})
            per[self.name] = round(value, 2)
            self.app.settings["window_opacity"] = per
            if hasattr(self.app, "opacity_changed"):
                self.app.opacity_changed(self.name, round(value, 2))

    # -- solid mode: cut the window down to its content, backdrop behind it
    def solid_rects(self):
        """Rectangles (x, y, w, h in window coordinates) that hold content, or
        None if not ready. Solid windows implement this."""
        return None

    @staticmethod
    def shape_wanted():
        """Cutting the window to its content only works where cut-away areas are
        really see-through (X11). XWayland shows the never-painted memory of a cut
        area instead (leftover graphics from other windows), so there the window is
        kept fully painted and just made tight around its content."""
        return not on_wayland()

    def sync_solid(self, force=False):
        if not self.use_solid:
            if self._shape_wid:
                self.app.clickthrough.set_shape(self.win, None)
                self._shape_key, self._shape_wid = None, 0
            if self._backdrop is not None:
                self._backdrop.hide()
            return
        try:
            rects = self.solid_rects()
            if rects is None:
                return
            wid = self.app.clickthrough.window_id(self.win)
            shape = rects if self.shape_wanted() else None
            key = None if shape is None else tuple(shape)
            if force or key != self._shape_key or wid != self._shape_wid:
                got = self.app.clickthrough.set_shape(self.win, shape)
                if got:
                    self._shape_key, self._shape_wid = key, got
            box = None
            if rects:
                pad = Backdrop.PAD
                x0 = min(r[0] for r in rects) - pad
                y0 = min(r[1] for r in rects) - pad
                x1 = max(r[0] + r[2] for r in rects) + pad
                y1 = max(r[1] + r[3] for r in rects) + pad
                box = (x0, y0, x1 - x0, y1 - y0)
            self.backdrop().place(box, force=force)
        except tk.TclError:
            pass

    def _on_geometry(self, e):
        if e.widget is self.win and self.use_solid:
            self.sync_solid()

    def _on_unmap(self, e):
        if e.widget is self.win and self._backdrop is not None:
            self._backdrop.hide()

    def _wheel_opacity(self, e, step):
        if e.num == 5 or getattr(e, "delta", 0) < 0:
            step = -step
        self.set_opacity(round(self.opacity() + step, 2))
        if not self.use_solid:   # solid windows are cut to their content; the slider shows it
            self._toast(f"Opacity {int(round(self.opacity() * 100))}%")
        return "break"

    def _toast(self, text):
        lab = getattr(self, "_toast_label", None)
        if lab is None:
            lab = self._toast_label = tk.Label(self.win, bg=P["accent"], fg=P["bg"],
                                               font=self.app.fonts["bold"], padx=10, pady=4)
        lab.configure(text=text)
        lab.place(relx=0.5, rely=0.5, anchor="center")
        lab.lift()
        if getattr(self, "_toast_job", None):
            self.win.after_cancel(self._toast_job)
        self._toast_job = self.win.after(900, lab.place_forget)

    def is_visible(self):
        # A ghosted overlay is hidden only temporarily: it still counts as shown.
        try:
            return self._ghosted or self._auto_hidden or self.win.state() != "withdrawn"
        except tk.TclError:
            return False

    def set_locked(self, locked):
        """Locked = borderless and click-through; unlocked = a window you can
        click, move and resize."""
        self.locked = bool(locked)
        self.apply_chrome()

    def passthrough_when_locked(self):
        """Should this window let clicks through while overlays are locked?
        Per-window choice; the map stays interactive by default."""
        choice = (self.app.settings["clickthrough_windows"] or {}).get(self.name)
        return (self.name != "map") if choice is None else bool(choice)

    @property
    def passthrough(self):
        return self.locked and self.passthrough_when_locked()

    @property
    def clickable(self):
        return not self.passthrough

    @property
    def movable(self):
        """Can be dragged/resized by hand: borderless and not locked in place."""
        return self.borderless and not self.locked

    def apply_chrome(self):
        """Title bar / click-through state from the settings:
        - locked: fixed in place and always on top (unmanaged overlay, kept
          raised); clicks pass through or not per window (the map stays
          interactive by default: pan and zoom still work)
        - "hide title bars" (unlocked): no title bar but a normal window you
          can move, resize and type in
        - otherwise: a normal window."""
        app = self.app
        passthrough = self.passthrough
        solid = self.use_solid
        # Solid windows are always borderless overlays: the window and its
        # backdrop must share one stacking layer, which only unmanaged windows do.
        hide = self.locked or bool(app.settings["hide_borders"]) or solid
        unmanaged = self.locked or solid or (hide and not app.decorations.available)
        visible = self.is_visible()
        self._ghosted = self._auto_hidden = False
        if visible:
            self.remember()
            self.win.withdraw()
        try:
            self.win.overrideredirect(unmanaged)
        except tk.TclError:
            pass
        if app.decorations.available:
            app.decorations.set(self.win, decorated=not hide)
        self.apply_input()
        self.borderless = hide
        self.unmanaged = unmanaged
        self.on_chrome()
        self._update_grip()
        try:  # locked overlays are always on top, whatever the setting says
            self.win.attributes("-topmost", True if (self.locked or self.always_top)
                                else bool(app.settings["always_on_top"]))
        except tk.TclError:
            pass
        if visible:
            self.win.deiconify()
            self.win.after(80, self._reassert)
        self.win.after(100, lambda: self.set_opacity(self.opacity(), save=False))

    def fits_height(self):
        """True for windows that size their own height to their content."""
        return False

    def interactive_rects(self):
        """Hook: parts of the window (x, y, w, h in window coordinates) that must stay
        clickable while the rest lets clicks through, such as a scroll strip."""
        return []

    def apply_input(self):
        """Tell the X server what this window lets through: nothing but any interactive
        strip while it is click-through, everything when it is not."""
        ct = self.app.clickthrough
        if not self.passthrough:
            ct.set(self.win, False)
            return
        rects = [r for r in self.interactive_rects() if r[2] > 0 and r[3] > 0]
        if not (rects and ct.set_input(self.win, rects)):
            ct.set(self.win, True)

    hold_ghost = False       # True while the mouse is working a control, so the window must not hide

    def ghost_rect(self):
        """Where the mouse hides this overlay (default: the whole window)."""
        return (self.win.winfo_rootx(), self.win.winfo_rooty(),
                self.win.winfo_width(), self.win.winfo_height())

    def on_chrome(self):
        """Hook: called when the title bar / lock state changed."""

    def geometry_for_saving(self, geometry):
        """Hook: the geometry string to remember (default: as is)."""
        return geometry

    # -- moving and resizing without a title bar
    def draggable(self, *widgets):
        for w in widgets:
            w.bind("<ButtonPress-1>", self._drag_start, add="+")
            w.bind("<B1-Motion>", self._drag_move, add="+")

    def _drag_start(self, e):
        # Any unlocked window can be dragged by its content too, in case the title bar
        # is missing (some window managers do not bring it back after an unlock).
        if self.locked:
            self._drag_from = None
            return
        self._drag_from = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())

    def _drag_move(self, e):
        if not getattr(self, "_drag_from", None):
            return
        x0, y0, wx, wy = self._drag_from
        self.win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _update_grip(self):
        if not hasattr(self, "_grip"):
            self._grip = ttk.Sizegrip(self.win)
            self._grip.bind("<ButtonPress-1>", self._resize_start)
            self._grip.bind("<B1-Motion>", self._resize_move)
        if not self.locked:
            self._grip.place(relx=1.0, rely=1.0, anchor="se")
            self._grip.lift()
        else:
            self._grip.place_forget()
        self._show_handle(self.movable)

    def _show_handle(self, show):
        if show and not self._handle_shown:
            others = [w for w in self.win.pack_slaves() if w is not self._handle]
            self._handle.pack(side="top", fill="x", **({"before": others[0]} if others else {}))
            self._handle_shown = True
        elif not show and self._handle_shown:
            self._handle.pack_forget()
            self._handle_shown = False

    # Sizegrip relies on the window manager, which unmanaged windows don't
    # have, so resize by hand (works for both kinds).
    def _resize_start(self, e):
        if self.locked:
            self._resize_from = None
            return "break"
        self._resize_from = (e.x_root, e.y_root, self.win.winfo_width(), self.win.winfo_height())
        return "break"

    def _resize_move(self, e):
        if not getattr(self, "_resize_from", None):
            return "break"
        x0, y0, w0, h0 = self._resize_from
        w = max(160, w0 + e.x_root - x0)
        h = h0 if self.fits_height() else max(80, h0 + e.y_root - y0)
        self.win.geometry(f"{w}x{h}")
        return "break"

    def raise_all(self):
        """Above everything else, with the backdrop just below the window."""
        try:
            self.win.lift()
        except tk.TclError:
            pass
        if self._backdrop is not None:
            self._backdrop.stack()

    def _reassert(self):
        # Some window managers only settle the window after it is mapped.
        if self.locked or self.unmanaged:
            self.apply_input()
            self.raise_all()
        if self.use_solid:
            self.sync_solid(force=True)

    def maintain(self):
        """Once a second: put right anything the window manager or Tk undid
        (the click-through and shape live on an X window that Tk may have
        recreated), and keep overlays on top."""
        if not self.is_visible() or self._ghosted or self._auto_hidden:
            return
        if self.locked or self.unmanaged:
            self.apply_input()
        if self.use_solid:
            # Not forced: the shape and the panel are touched only if their contents changed
            # or Tk has given the window a new X window. Re-sending them every second
            # makes some compositors rebuild the window each time.
            self.sync_solid()
        if self.locked or self.always_top or (self.unmanaged and self.app.settings["always_on_top"]):
            self.raise_all()
        elif self._backdrop is not None:
            self._backdrop.stack()

    def remap(self):
        """Unmap the window and map it again. This is what hover-hide does as the mouse
        passes over a window, and it repaints the window and puts it, and its panel,
        back in order; "Refresh overlays" does it on request (or on a timer)."""
        if not self.is_visible() or self._ghosted or self._auto_hidden:
            return
        try:
            self.win.withdraw()
            if self._backdrop is not None:
                self._backdrop.hide()
            self.win.update_idletasks()
            self.win.deiconify()
            self.raise_all()
            self.win.after(80, self._reassert)
        except tk.TclError:
            pass

    def keep_on_top(self):
        """Several times a second, for windows that must never be hidden: a click
        on the game raises the game above an overlay, so put the overlay back at
        once. Skipped while a menu, drop-down or dialog holds the pointer, or the
        raise would bury it."""
        if not self.always_top or not self.is_visible() or self._ghosted or self._auto_hidden:
            return
        try:
            if self.app.root.grab_current():
                return
            self.win.lift()
        except tk.TclError:
            return
        if self._backdrop is not None:
            self._backdrop.stack()

    def show(self):
        self.win.deiconify()
        self.raise_all()
        if self.locked or self.unmanaged:
            self.win.after(80, self._reassert)

    # -- Wayland workaround: get out of the way of the mouse
    GHOST_MARGIN = 4      # px of slack before the overlay comes back
    GHOST_STALE = 4.0     # s without pointer updates: assume it isn't being tracked

    @staticmethod
    def _inside(rect, px, py, margin=0):
        x, y, w, h = rect
        return x - margin <= px < x + w + margin and y - margin <= py < y + h + margin

    def update_ghost(self, px, py, now):
        """On a Wayland desktop a click on an overlay window is lost whatever its
        shape, so true click-through is impossible. Instead, hide a locked overlay
        while the mouse is over it; the click then reaches the game, and the
        overlay returns when the mouse leaves."""
        try:
            active = self.passthrough and self.app.ghost_enabled()
            if not self._ghosted:
                if not active or not self.is_visible() or self._auto_hidden:
                    return
                if self._ghost_block:
                    if abs(px - self._ghost_block[0]) < 4 and abs(py - self._ghost_block[1]) < 4:
                        return
                    self._ghost_block = None
                if self.hold_ghost:
                    return
                rect = self.ghost_rect()
                if self._inside(rect, px, py):
                    self._ghost_rect, self._ghost_pos, self._ghost_since = rect, (px, py), now
                    self._ghosted = True            # set first, so is_visible() stays True
                    self.win.withdraw()
                    if self._backdrop is not None:
                        self._backdrop.hide()
                return
            if (px, py) != self._ghost_pos:
                self._ghost_pos, self._ghost_since = (px, py), now
            if not active or not self._inside(self._ghost_rect, px, py, self.GHOST_MARGIN):
                self._unghost()
            elif now - self._ghost_since > self.GHOST_STALE:
                # The desktop stopped telling us where the pointer is (it is over a
                # window that isn't an X11 client): don't stay hidden forever.
                self._ghost_block = (px, py)
                self._unghost()
        except tk.TclError:
            pass

    def _unghost(self):
        self._ghosted = False
        self.win.deiconify()
        self.raise_all()
        self.win.after(80, self._reassert)

    def _set_auto_hidden(self, hide):
        """Unmap the window while there is nothing to show (a window with no
        content would otherwise be a strip of leftover video memory)."""
        if hide == self._auto_hidden:
            return
        try:
            if hide:
                if self._ghosted or self.win.state() == "withdrawn":
                    return
                self._auto_hidden = True
                self.win.withdraw()
                if self._backdrop is not None:
                    self._backdrop.hide()
            else:
                self._auto_hidden = False
                if not self._ghosted:
                    self.win.deiconify()
                    self.raise_all()
                    self.win.after(80, self._reassert)
        except tk.TclError:
            pass

    def hide(self):
        self._ghosted = self._auto_hidden = False
        self.win.withdraw()
        if self._backdrop is not None:
            self._backdrop.hide()
        self.remember()
        self.app.on_window_toggled()

    def toggle(self):
        if self.is_visible():
            self.hide()
        else:
            self.show()
            self.app.on_window_toggled()

    def remember(self):
        try:
            wins = dict(self.app.settings["windows"] or {})
            wins[self.name] = {"geometry": self.geometry_for_saving(self.win.geometry()),
                               "visible": self.is_visible()}
            self.app.settings["windows"] = wins
        except tk.TclError:
            pass

    def canvas(self, parent):
        c = tk.Canvas(parent, bg=P["bg"], highlightthickness=0, bd=0)
        return c
