"""Read-only inspection of the live X11 state (no tkinter needed)."""
import ctypes
import ctypes.util
import os

from . import xguard

SHAPE_BOUNDING, SHAPE_INPUT = 0, 2


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int),
        ("height", ctypes.c_int), ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong), ("class_", ctypes.c_int),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong), ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p),
    ]


class _XRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short),
                ("w", ctypes.c_ushort), ("h", ctypes.c_ushort)]


def on_wayland():
    """True in a Wayland session (X11 apps then run through XWayland)."""
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


def _lib(name):
    path = ctypes.util.find_library(name)
    if not path:
        raise OSError(f"lib{name} not found")
    return ctypes.CDLL(path)


class XInspector:
    """Read-only look at the X server. `available` is False off X11."""

    def __init__(self, loader=_lib):
        self.available = False
        self.reason = ""
        if os.environ.get("EQTOOL_NO_XTRICKS", "").strip() not in ("", "0"):
            self.reason = "turned off by EQTOOL_NO_XTRICKS"
            return
        try:
            x, xe = loader("X11"), loader("Xext")
            c = ctypes
            x.XOpenDisplay.restype = c.c_void_p
            x.XOpenDisplay.argtypes = [c.c_char_p]
            x.XDefaultRootWindow.restype = c.c_ulong
            x.XDefaultRootWindow.argtypes = [c.c_void_p]
            x.XInternAtom.restype = c.c_ulong
            x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
            x.XGetWindowAttributes.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(_XWindowAttributes)]
            x.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong),
                                     c.POINTER(c.c_ulong), c.POINTER(c.POINTER(c.c_ulong)),
                                     c.POINTER(c.c_uint)]
            x.XFree.argtypes = [c.c_void_p]
            x.XGetWindowProperty.argtypes = [
                c.c_void_p, c.c_ulong, c.c_ulong, c.c_long, c.c_long, c.c_int, c.c_ulong,
                c.POINTER(c.c_ulong), c.POINTER(c.c_int), c.POINTER(c.c_ulong),
                c.POINTER(c.c_ulong), c.POINTER(c.c_void_p)]
            x.XQueryPointer.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong),
                                        c.POINTER(c.c_ulong), c.POINTER(c.c_int),
                                        c.POINTER(c.c_int), c.POINTER(c.c_int),
                                        c.POINTER(c.c_int), c.POINTER(c.c_uint)]
            x.XTranslateCoordinates.argtypes = [
                c.c_void_p, c.c_ulong, c.c_ulong, c.c_int, c.c_int, c.POINTER(c.c_int),
                c.POINTER(c.c_int), c.POINTER(c.c_ulong)]
            x.XGetSelectionOwner.restype = c.c_ulong
            x.XGetSelectionOwner.argtypes = [c.c_void_p, c.c_ulong]
            x.XFetchName.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_char_p)]
            xe.XShapeGetRectangles.restype = c.POINTER(_XRect)
            xe.XShapeGetRectangles.argtypes = [c.c_void_p, c.c_ulong, c.c_int,
                                               c.POINTER(c.c_int), c.POINTER(c.c_int)]
            dpy = x.XOpenDisplay(None)
            if not dpy:
                raise OSError("cannot open the X display")
            self._x, self._xe, self._dpy = x, xe, dpy
            xguard.watch(x, dpy)
            self.root = x.XDefaultRootWindow(dpy)
            self.available = True
        except Exception as e:
            self.reason = str(e)

    # ------------------------------------------------------------ primitives
    def atom(self, name):
        return self._x.XInternAtom(self._dpy, name.encode(), 0)

    def attrs(self, wid):
        a = _XWindowAttributes()
        ok = self._x.XGetWindowAttributes(self._dpy, wid, ctypes.byref(a))
        return a if ok else None

    def tree(self, wid):
        root, parent, kids, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
        if not self._x.XQueryTree(self._dpy, wid, ctypes.byref(root), ctypes.byref(parent),
                                  ctypes.byref(kids), ctypes.byref(n)):
            return 0, []
        children = [kids[i] for i in range(n.value)] if kids else []
        if kids:
            self._x.XFree(kids)
        return parent.value, children

    def rects(self, wid, kind):
        """The window's shape as [(x, y, w, h)]. A window with no shape set
        reports one rectangle covering the whole window; an empty list means an
        empty region (X returns a null pointer with a count of zero for that)."""
        n, order = ctypes.c_int(), ctypes.c_int()
        r = self._xe.XShapeGetRectangles(self._dpy, wid, kind, ctypes.byref(n), ctypes.byref(order))
        if not r:
            return []
        out = [(r[i].x, r[i].y, r[i].w, r[i].h) for i in range(n.value)]
        self._x.XFree(r)
        return out

    def _prop(self, wid, name, length=64):
        t, f, n, after, data = (ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(),
                                ctypes.c_ulong(), ctypes.c_void_p())
        self._x.XGetWindowProperty(self._dpy, wid, self.atom(name), 0, length, 0, 0,
                                   ctypes.byref(t), ctypes.byref(f), ctypes.byref(n),
                                   ctypes.byref(after), ctypes.byref(data))
        if not n.value or not data.value:
            return None, 0, 0
        return data, n.value, f.value

    def _cards(self, wid, name, length=32):
        """The 32-bit values of a property (CARDINAL or ATOM), or []."""
        data, n, fmt = self._prop(wid, name, length)
        if data is None:
            return []
        try:
            arr = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            return [arr[i] & 0xFFFFFFFF for i in range(n)]
        finally:
            self._x.XFree(data)

    def compositing_hints(self):
        """Top-level windows that ask the compositor to step aside, or are fullscreen:
        [(name, bypass_value or None, fullscreen)]. A game that sets
        _NET_WM_BYPASS_COMPOSITOR = 1 (Wine does, for fullscreen windows) can make the
        desktop stop compositing while it is up, and then window opacity, shapes and
        panels over it behave differently."""
        out = []
        full_atom = self.atom("_NET_WM_STATE_FULLSCREEN") & 0xFFFFFFFF
        _, kids = self.tree(self.root)
        for frame in kids:
            a = self.attrs(frame)
            if not a or a.map_state != 2:
                continue
            bypass, full = None, False
            for w in [frame] + self.tree(frame)[1][:8]:
                b = self._cards(w, "_NET_WM_BYPASS_COMPOSITOR", 1)
                if b and bypass is None:
                    bypass = b[0]
                if full_atom in self._cards(w, "_NET_WM_STATE", 32):
                    full = True
            if bypass == 1 or full:        # 1 = bypass please; 2 = please do NOT bypass (Chrome, Discord)
                out.append((self.toplevel_name(frame) or f"0x{frame:x}", bypass, full))
        return out

    def opacity(self, wid):
        """0..1 as the compositor sees it, or None if the window has no hint."""
        data, n, _ = self._prop(wid, "_NET_WM_WINDOW_OPACITY", 1)
        if data is None:
            return None
        raw = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0] & 0xFFFFFFFF
        self._x.XFree(data)
        return raw / 0xFFFFFFFF

    def name(self, wid):
        data, n, fmt = self._prop(wid, "_NET_WM_NAME", 256)
        if data is not None:
            try:
                return ctypes.string_at(data, n).decode("utf-8", "replace")
            finally:
                self._x.XFree(data)
        s = ctypes.c_char_p()
        if self._x.XFetchName(self._dpy, wid, ctypes.byref(s)) and s.value:
            return s.value.decode("latin-1", "replace")
        return ""

    def toplevel_name(self, top):
        """Name of a root child: its own, or that of the client inside a WM frame."""
        todo, seen = [top], 0
        while todo and seen < 12:
            w = todo.pop(0)
            seen += 1
            nm = self.name(w)
            if nm:
                return nm
            todo.extend(self.tree(w)[1])
        return ""

    def window_manager(self):
        data, n, _ = self._prop(self.root, "_NET_SUPPORTING_WM_CHECK", 1)
        if data is None:
            return ""
        wid = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0]
        self._x.XFree(data)
        return self.name(wid)

    def compositor_running(self, screen=0):
        """A compositing manager owns _NET_WM_CM_S<screen>. Without one, window
        opacity has no effect and 'transparent' windows are simply opaque."""
        return bool(self._x.XGetSelectionOwner(self._dpy, self.atom(f"_NET_WM_CM_S{screen}")))

    def pointer(self):
        r, ch = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy, mask = (ctypes.c_int(), ctypes.c_int(), ctypes.c_int(),
                                ctypes.c_int(), ctypes.c_uint())
        self._x.XQueryPointer(self._dpy, self.root, ctypes.byref(r), ctypes.byref(ch),
                              ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(wx),
                              ctypes.byref(wy), ctypes.byref(mask))
        return rx.value, ry.value

    def chain_at(self, px, py):
        """Windows from the top level down to the one that would get a click at
        (px, py). Input shapes are honoured, exactly as for a real click."""
        out, cur = [], self.root
        dx, dy, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        for _ in range(32):
            self._x.XTranslateCoordinates(self._dpy, self.root, cur, px, py, ctypes.byref(dx),
                                          ctypes.byref(dy), ctypes.byref(child))
            if not child.value:
                break
            out.append(child.value)
            cur = child.value
        return out

    def above(self, top, limit=6):
        """(name, w, h) of visible top-level windows stacked above `top` that
        overlap it."""
        _, kids = self.tree(self.root)
        if top not in kids:
            return []
        me = self.attrs(top)
        out = []
        for w in kids[kids.index(top) + 1:]:
            a = self.attrs(w)
            if not a or a.map_state != 2 or not me:
                continue
            if (a.x < me.x + me.width and a.x + a.width > me.x and
                    a.y < me.y + me.height and a.y + a.height > me.y):
                out.append((self.toplevel_name(w) or f"0x{w:x}", a.width, a.height))
        return out[-limit:]


VANISHED = ("If you clicked INSIDE the red square and nothing was recorded, the click vanished: "
            "the overlay did not catch it and the button underneath never saw it. That is what "
            "XWayland does on Wayland desktops: the desktop hands the click to the overlay's "
            "window and the X server then drops it, so click-through cannot work there.")


def conclude(through, blocked, background, margin=None):
    """Plain-language reading of the click-through test counts.

    `through` counts clicks that landed inside a red square and still reached the
    button underneath; `margin` counts clicks on a button's grey border (outside
    the red square), which say nothing about the test. Test D is the hover-hide
    overlay: it should hide itself when the mouse is over it, so a click there
    reaches the button. Only clicks that arrive are visible, so "nothing
    recorded" is worded as such."""
    margin = margin or {}
    if not (any(through.values()) or any(blocked.values()) or background or any(margin.values())):
        return ("No clicks recorded yet. Click the grey control button C first, then INSIDE each "
                "red square a few times. For D, move the mouse onto the red square, wait for it "
                "to vanish, then click where it was.")
    parts = ["Control button C works." if through["C"] else
             "Control button C has not been clicked (if clicking it does nothing, this window "
             "is not receiving clicks at all)."]
    for k in ("A", "B", "D"):
        if k not in through:
            continue
        t, b, m = through[k], blocked.get(k, 0), margin.get(k, 0)
        hover = k == "D"
        name = "D (hover-hide)" if hover else k
        if t and not b:
            parts.append(f"{name}: " + ("WORKS - the overlay got out of the way and the click "
                                        "reached the button." if hover else "click-through WORKS."))
        elif b and not t:
            parts.append(f"{name}: BLOCKED - the overlay caught the click"
                         + (" before it could hide." if hover else "."))
        elif t and b:
            parts.append(f"{name}: mixed ({t} passed, {b} caught).")
        elif m:
            parts.append(f"{name}: {m} click(s) hit the grey border outside the red square; those "
                         "do not test it. " + (HOVER_NOTHING if hover else VANISHED))
        elif background and not hover:
            parts.append(f"{name}: nothing reached its button or overlay. Clicks did reach this "
                         "window's background: if you were clicking inside red square " + k +
                         ", it passed through the overlay but the overlay is not over the button.")
        else:
            parts.append(f"{name}: nothing recorded. " + (HOVER_NOTHING if hover else VANISHED))
    return " ".join(parts)


HOVER_NOTHING = ("If the red square vanished when the mouse reached it and you clicked where it was "
                 "but nothing registered, hover-hide does not help on this desktop (look at the "
                 "pointer line to see whether the desktop is reporting the mouse). If it never "
                 "vanished, the pointer position is not being reported at all.")
