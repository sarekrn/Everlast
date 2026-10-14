"""Make a Tk window let mouse clicks pass through to whatever is underneath.

X11 (Linux, including XWayland): give the window an empty *input shape* with
the X Shape extension (libXext). The window still draws normally, but the X
server delivers clicks to the window below it.
Windows: the WS_EX_TRANSPARENT | WS_EX_LAYERED extended styles.

Everything is loaded with ctypes from libraries already on the system; if they
aren't there, `available` is False and nothing else changes.
"""
import ctypes
import ctypes.util
import logging
import os
import sys

from . import xguard

log = logging.getLogger(__name__)

SHAPE_BOUNDING = 0   # ShapeBounding (what is visible)
SHAPE_INPUT = 2      # ShapeInput (where clicks land)
SHAPE_SET = 0        # ShapeSet
UNSORTED = 0         # Unsorted


def _setup_tree_calls(x):
    """Declare the Xlib calls used to find a window's ancestors."""
    c = ctypes
    x.XDefaultRootWindow.restype = c.c_ulong
    x.XDefaultRootWindow.argtypes = [c.c_void_p]
    x.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_ulong),
                             c.POINTER(c.POINTER(c.c_ulong)), c.POINTER(c.c_uint)]
    x.XFree.argtypes = [c.c_void_p]
    x.XTranslateCoordinates.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_int, c.c_int,
                                        c.POINTER(c.c_int), c.POINTER(c.c_int),
                                        c.POINTER(c.c_ulong)]


def _flush_tk(win):
    """Make Tk send its buffered requests to the X server. Tk keeps its own connection
    and only flushes now and then, so a window it has just created or replaced may not
    exist yet as far as OUR connection can tell. A round trip (asking for the pointer
    position) forces the flush."""
    try:
        win.update_idletasks()
        win.winfo_pointerxy()
    except Exception:
        pass


def _toplevel_chain(x, dpy, inner):
    """[(window id, dx, dy), ...] from Tk's own toplevel window `inner` out to the
    top-level window, the one whose parent is the root. (dx, dy) is where `inner`'s
    top-left corner sits in that window's own coordinates.

    A Tk toplevel is not one X window but a stack: its own window lives inside a
    "wrapper" window (and, for a decorated window, inside the window manager's frame).
    The screen, the compositor and the pointer all deal with the OUTERMOST one, so a
    shape or an input region set on Tk's inner window alone leaves the wrapper
    fully clickable and unshaped. `wm frame` and `winfo id` both name the inner one."""
    c = ctypes
    root = x.XDefaultRootWindow(dpy)
    ids, cur = [], inner
    for _ in range(12):
        if not cur or cur == root:
            break
        r, parent, kids, n = c.c_ulong(), c.c_ulong(), c.POINTER(c.c_ulong)(), c.c_uint()
        if not x.XQueryTree(dpy, cur, c.byref(r), c.byref(parent), c.byref(kids), c.byref(n)):
            break               # the window is gone (or never was): use what we have so far
        if kids:
            x.XFree(kids)
        ids.append(cur)         # only windows the server has just confirmed
        if parent.value in (0, root):
            break
        cur = parent.value
    out = []
    for wid in ids:
        dx, dy, child = c.c_int(), c.c_int(), c.c_ulong()
        if x.XTranslateCoordinates(dpy, inner, wid, 0, 0, c.byref(dx), c.byref(dy), c.byref(child)):
            out.append((wid, dx.value, dy.value))
        else:
            break
    return out


class _XRectangle(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short),
                ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]


SWITCH_OFF = "EQTOOL_NO_XTRICKS"     # set it to 1 to turn off every direct X window trick


def tricks_disabled():
    return os.environ.get(SWITCH_OFF, "").strip() not in ("", "0")


class _XImage(ctypes.Structure):
    """The leading fields of Xlib's XImage (enough to read the pixels)."""
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
                ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int),
                ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int)]


class ClickThrough:
    def __init__(self, root, loader=None):
        self.available = False
        self.reason = ""
        self._dpy = None
        self._x = self._xext = self._user32 = None
        if tricks_disabled():
            self.reason = f"turned off by {SWITCH_OFF}"
            return
        try:
            system = root.tk.call("tk", "windowingsystem")
        except Exception:
            system = ""
        try:
            if system == "x11":
                self._init_x11(loader or self._load)
            elif system == "win32" and sys.platform.startswith("win"):
                self._user32 = ctypes.windll.user32
                self.available = True
            else:
                self.reason = f"not supported on the '{system or 'unknown'}' window system"
        except Exception as e:
            log.exception("click-through unavailable")
            self.reason = str(e)
            self.available = False

    # ------------------------------------------------------------------ X11
    @staticmethod
    def _load(name):
        path = ctypes.util.find_library(name)
        if not path:
            raise OSError(f"lib{name} not found")
        return ctypes.CDLL(path)

    def _init_x11(self, loader):
        x, xext = loader("X11"), loader("Xext")
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XFlush.argtypes = [ctypes.c_void_p]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _setup_tree_calls(x)
        xext.XShapeQueryExtension.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                              ctypes.POINTER(ctypes.c_int)]
        xext.XShapeQueryVersion.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                            ctypes.POINTER(ctypes.c_int)]
        xext.XShapeCombineRectangles.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        xext.XShapeCombineMask.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_ulong, ctypes.c_int]
        dpy = x.XOpenDisplay(None)
        if not dpy:
            raise OSError("cannot open X display")
        ev, er, major, minor = (ctypes.c_int() for _ in range(4))
        if not xext.XShapeQueryExtension(dpy, ctypes.byref(ev), ctypes.byref(er)):
            raise OSError("X server has no Shape extension")
        xext.XShapeQueryVersion(dpy, ctypes.byref(major), ctypes.byref(minor))
        if (major.value, minor.value) < (1, 1):  # input shapes arrived in Shape 1.1
            raise OSError(f"X Shape {major.value}.{minor.value} has no input shapes")
        self._x, self._xext, self._dpy = x, xext, dpy
        xguard.watch(x, dpy)
        self.available = True

    # ------------------------------------------------------------------ API
    @staticmethod
    def _frame_id(win):
        """What Tk calls the frame (the HWND on Windows)."""
        try:
            win.update_idletasks()
            return int(win.wm_frame(), 16)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def inner_id(win):
        """Tk's own toplevel window."""
        try:
            return int(win.winfo_id())
        except (ValueError, TypeError):
            return 0

    def chain(self, win):
        """[(id, dx, dy)] from Tk's toplevel window out to the real top-level (X11)."""
        if not self._dpy:
            return []
        _flush_tk(win)
        inner = self.inner_id(win)
        return _toplevel_chain(self._x, self._dpy, inner) if inner else []

    def window_id(self, win):
        """The outermost window: what the screen shows and clicks land on (0 if the
        window is not on screen yet)."""
        if self._dpy:
            ch = self.chain(win)
            return ch[-1][0] if ch else 0
        return self._frame_id(win)

    @property
    def shape_available(self):
        """True where windows can be cut to a shape (X11 with the Shape extension)."""
        return bool(self.available and self._dpy)

    def set(self, win, passthrough):
        """Turn click-through on/off for a Toplevel. Returns True on success. Applied
        to every window in its stack (Tk's window, the wrapper around it, and the
        window manager's frame if there is one), because clicks are decided by the
        outermost."""
        if not self.available:
            return False
        try:
            if not win.winfo_exists():
                return False
            if self._dpy:
                ch = self.chain(win)
                if not ch:
                    return False
                for wid, _dx, _dy in ch:
                    if passthrough:  # empty input region: every click goes through
                        self._xext.XShapeCombineRectangles(self._dpy, wid, SHAPE_INPUT, 0, 0,
                                                           None, 0, SHAPE_SET, UNSORTED)
                    else:            # no mask: back to the normal, clickable shape
                        self._xext.XShapeCombineMask(self._dpy, wid, SHAPE_INPUT, 0, 0, 0,
                                                     SHAPE_SET)
                self._x.XFlush(self._dpy)
                return True
            if self._user32:
                GWL_EXSTYLE, LAYERED, TRANSPARENT = -20, 0x80000, 0x20
                wid = self._frame_id(win)
                if not wid:
                    return False
                style = self._user32.GetWindowLongW(wid, GWL_EXSTYLE)
                style = (style | LAYERED | TRANSPARENT) if passthrough else (style & ~TRANSPARENT)
                self._user32.SetWindowLongW(wid, GWL_EXSTYLE, style)
                return True
        except Exception:
            log.exception("click-through change failed")
        return False

    def set_input(self, win, rects):
        """Click-through except for these rectangles (x, y, w, h in the Tk window's
        own coordinates): clicks there land on the window, clicks anywhere else go to
        whatever is underneath. Used for a slim scroll strip on an otherwise
        click-through overlay. X11 only; elsewhere it returns False and the caller
        falls back to plain click-through."""
        if not self.shape_available:
            return False
        try:
            if not win.winfo_exists():
                return False
            ch = self.chain(win)
            if not ch:
                return False
            for wid, dx, dy in ch:
                self._combine_rects(wid, SHAPE_INPUT, [(x + dx, y + dy, w, h) for x, y, w, h in rects])
            self._x.XFlush(self._dpy)
            return True
        except Exception:
            log.exception("input region change failed")
            return False

    def _combine_rects(self, wid, kind, rects):
        n = len(rects)
        arr = (_XRectangle * max(1, n))()
        for i, (x, y, w, h) in enumerate(rects):
            arr[i] = _XRectangle(max(-32000, min(32000, int(x))), max(-32000, min(32000, int(y))),
                                 max(0, min(65535, int(w))), max(0, min(65535, int(h))))
        self._xext.XShapeCombineRectangles(self._dpy, wid, kind, 0, 0,
                                           ctypes.cast(arr, ctypes.c_void_p), n,
                                           SHAPE_SET, UNSORTED)

    def glyph_rects(self, win, width, height):
        """Rectangles covering every pixel of the window that differs from its
        top-left pixel (the background): the letters and their outline. Used to cut
        a text window down to just its letters. Returns None if the pixels cannot be
        read (the caller then shows the whole window)."""
        if not self.shape_available:
            return None
        x = self._x
        try:
            x.XGetImage.restype = ctypes.POINTER(_XImage)
            x.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
            x.XDestroyImage.argtypes = [ctypes.POINTER(_XImage)]
            x.XSync(self._dpy, 0)
            img = x.XGetImage(self._dpy, self.inner_id(win), 0, 0, int(width), int(height),
                              0xFFFFFFFF, 2)           # ZPixmap
            if not img:
                return None
            try:
                im = img.contents
                if im.bits_per_pixel != 32 or not im.data:
                    return None
                raw = ctypes.string_at(im.data, im.bytes_per_line * im.height)
                per = im.bytes_per_line // 4
                px = memoryview(raw).cast("I")
                bg = px[0]
                rows = []
                for yy in range(im.height):
                    row = px[yy * per: yy * per + im.width]
                    runs, start = [], None
                    for xx, v in enumerate(row):
                        if v != bg:
                            if start is None:
                                start = xx
                        elif start is not None:
                            runs.append((start, xx - start))
                            start = None
                    if start is not None:
                        runs.append((start, im.width - start))
                    rows.append(tuple(runs))
            finally:
                x.XDestroyImage(img)
            rects, yy = [], 0
            while yy < len(rows):                        # merge identical rows into one block
                y2 = yy + 1
                while y2 < len(rows) and rows[y2] == rows[yy]:
                    y2 += 1
                for rx, rw in rows[yy]:
                    rects.append((rx, yy, rw, y2 - yy))
                yy = y2
            return rects
        except Exception:
            log.exception("reading the window's pixels failed")
            return None

    def set_shape(self, win, rects):
        """Show only these rectangles of the window (x, y, w, h in the Tk window's
        coordinates); everything else is cut away, so it is fully transparent and
        clicks go to whatever is underneath. rects=None restores the full window. The
        shape goes on every window in the stack, the outermost above all: a window
        left unshaped around a shaped one shows whatever it was last drawn with.
        Returns the outermost window's id, or 0."""
        if not self.shape_available:
            return 0
        try:
            if not win.winfo_exists():
                return 0
            ch = self.chain(win)
            if not ch:
                return 0
            for wid, dx, dy in ch:
                if rects is None:
                    self._xext.XShapeCombineMask(self._dpy, wid, SHAPE_BOUNDING, 0, 0, 0, SHAPE_SET)
                else:
                    self._combine_rects(wid, SHAPE_BOUNDING, [(x + dx, y + dy, w, h) for x, y, w, h in rects])
            self._x.XFlush(self._dpy)
            return ch[-1][0]
        except Exception:
            log.exception("window shape change failed")
            return 0


class Decorations:
    """Ask the window manager to hide (or show) a window's title bar and
    border, via the standard _MOTIF_WM_HINTS property. Unlike Tk's
    overrideredirect, the window stays managed: it can take keyboard focus,
    stays on top and shows up normally. X11 only; elsewhere `available` is
    False and callers fall back to overrideredirect."""

    MWM_HINTS_DECORATIONS = 2

    def __init__(self, root, loader=None):
        self.available = False
        self._dpy = None
        if tricks_disabled():
            return
        try:
            system = root.tk.call("tk", "windowingsystem")
        except Exception:
            system = ""
        try:
            if system != "x11":
                return
            x = (loader or ClickThrough._load)("X11")
            x.XOpenDisplay.restype = ctypes.c_void_p
            x.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x.XInternAtom.restype = ctypes.c_ulong
            x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            x.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                          ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_void_p, ctypes.c_int]
            x.XDeleteProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
            x.XFlush.argtypes = [ctypes.c_void_p]
            _setup_tree_calls(x)
            dpy = x.XOpenDisplay(None)
            if not dpy:
                return
            self._x, self._dpy = x, dpy
            xguard.watch(x, dpy)
            self._atom = x.XInternAtom(dpy, b"_MOTIF_WM_HINTS", 0)
            self._opacity_atom = x.XInternAtom(dpy, b"_NET_WM_WINDOW_OPACITY", 0)
            self.available = bool(self._atom)
        except Exception:
            log.exception("window decoration control unavailable")

    def _top(self, win):
        """The outermost window (the window manager and compositor read their
        properties from it)."""
        _flush_tk(win)
        inner = ClickThrough.inner_id(win)
        ch = _toplevel_chain(self._x, self._dpy, inner) if inner else []
        return ch[-1][0] if ch else 0

    def set(self, win, decorated):
        if not self.available:
            return False
        try:
            if not win.winfo_exists():
                return False
            wid = self._top(win)
            if not wid:
                return False
            if decorated:
                self._x.XDeleteProperty(self._dpy, wid, self._atom)
            else:
                # flags, functions, decorations, input_mode, status (format 32 = C longs)
                data = (ctypes.c_ulong * 5)(self.MWM_HINTS_DECORATIONS, 0, 0, 0, 0)
                self._x.XChangeProperty(self._dpy, wid, self._atom, self._atom, 32, 0,
                                        ctypes.cast(data, ctypes.c_void_p), 5)
            self._x.XFlush(self._dpy)
            return True
        except Exception:
            log.exception("could not change window decorations")
            return False

    def set_opacity(self, win, alpha):
        """Set the compositor opacity hint (_NET_WM_WINDOW_OPACITY) directly,
        so it holds for borderless/locked windows too. 1.0 removes it."""
        if not self.available:
            return False
        try:
            if not win.winfo_exists():
                return False
            wid = self._top(win)
            if not wid:
                return False
            alpha = max(0.0, min(1.0, float(alpha)))
            if alpha >= 0.999:
                self._x.XDeleteProperty(self._dpy, wid, self._opacity_atom)
            else:
                val = (ctypes.c_ulong * 1)(int(alpha * 0xFFFFFFFF))
                XA_CARDINAL = 6
                self._x.XChangeProperty(self._dpy, wid, self._opacity_atom, XA_CARDINAL, 32, 0,
                                        ctypes.cast(val, ctypes.c_void_p), 1)
            self._x.XFlush(self._dpy)
            return True
        except Exception:
            log.exception("could not set window opacity")
            return False
