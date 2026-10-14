"""System tray icon, with no extra packages: the XEmbed tray protocol over ctypes.

Every X11 desktop panel that still has a classic tray (Cinnamon, MATE, XFCE, KDE's
legacy tray, LXQt, ...) owns the selection `_NET_SYSTEM_TRAY_S<screen>`. A program
docks by asking that owner (a client message) to adopt a small window of its own and
then draws into it. That is all this does: it opens its own connection to the X
server, makes a window, asks to dock it, paints the Everlast icon and turns clicks
into callbacks.

`TrayIcon.docked` says whether a panel actually took the window. When there is no tray
the program must not hide itself (nothing could bring it back), so the caller checks
`docked` first. The tray appears and disappears with the panel; `poll()` notices and
docks again. ctypes only, no tkinter.
"""
import ctypes
import logging
import struct
import time
import zlib
from pathlib import Path

from . import xguard
from .xinspect import _lib

log = logging.getLogger(__name__)

EXPOSE, BUTTON_PRESS, DESTROY_NOTIFY, REPARENT_NOTIFY, CONFIGURE_NOTIFY = 12, 4, 17, 21, 22
CLIENT_MESSAGE = 33
EXPOSURE_MASK, BUTTON_PRESS_MASK, STRUCTURE_NOTIFY_MASK = 1 << 15, 1 << 2, 1 << 17
ZPIXMAP = 2
BACKGROUND = (0x16, 0x1d, 0x28)          # shown through the icon's rounded corners
SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


# ------------------------------------------------------------------ pictures
def decode_png(data):
    """Decode an 8-bit RGB / RGBA PNG (not interlaced) into (width, height, rows of
    RGBA tuples flattened as bytes). Enough for our own icon files."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, head = 8, [], None
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if head is None:
        raise ValueError("no IHDR")
    w, h, depth, ctype, _, _, interlace = head
    if depth != 8 or ctype not in (2, 6) or interlace:
        raise ValueError("unsupported PNG (need 8-bit RGB/RGBA, not interlaced)")
    bpp = 4 if ctype == 6 else 3
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    out = bytearray(w * 4 * h)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif ft != 0:
            raise ValueError("bad PNG filter")
        prev = line
        row = out[y * w * 4:(y + 1) * w * 4]
        if bpp == 4:
            out[y * w * 4:(y + 1) * w * 4] = line
        else:
            for x in range(w):
                row[x * 4:x * 4 + 3] = line[x * 3:x * 3 + 3]
                row[x * 4 + 3] = 255
            out[y * w * 4:(y + 1) * w * 4] = row
    return w, h, bytes(out)


def scale_rgba(w, h, px, size):
    """Box-filter an RGBA picture to size x size (premultiplied, so edges stay clean)."""
    if (w, h) == (size, size):
        return px
    out = bytearray(size * size * 4)
    for oy in range(size):
        y0, y1 = oy * h // size, max(oy * h // size + 1, (oy + 1) * h // size)
        for ox in range(size):
            x0, x1 = ox * w // size, max(ox * w // size + 1, (ox + 1) * w // size)
            r = g = b = a = n = 0
            for y in range(y0, y1):
                base = (y * w + x0) * 4
                for x in range(x0, x1):
                    al = px[base + 3]
                    r += px[base] * al
                    g += px[base + 1] * al
                    b += px[base + 2] * al
                    a += al
                    n += 1
                    base += 4
            o = (oy * size + ox) * 4
            if a:
                out[o], out[o + 1], out[o + 2] = r // a, g // a, b // a
            out[o + 3] = a // n
    return bytes(out)


def flatten(px, bg=BACKGROUND):
    """RGBA bytes -> list of (r, g, b) blended over `bg`."""
    out = []
    for i in range(0, len(px), 4):
        r, g, b, a = px[i:i + 4]
        if a == 255:
            out.append((r, g, b))
        else:
            out.append(tuple((c * a + k * (255 - a)) // 255 for c, k in zip((r, g, b), bg)))
    return out


def best_size(want):
    """The smallest stock icon at least `want` px (or the largest there is)."""
    return next((s for s in SIZES if s >= want), SIZES[-1])


class _XClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                ("data", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("msg", _XClientMessage), ("pad", ctypes.c_long * 24)]


class _XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.c_void_p)]


class TrayIcon:
    def __init__(self, icon_dir, on_click=None, on_menu=None, title="Everlast",
                 loader=_lib, size=24):
        self.available = False
        self.docked = False
        self.reason = ""
        self.on_click, self.on_menu = on_click, on_menu
        self.title = title
        self.size = size
        self._dir = Path(icon_dir)
        self._pix = {}
        self._img = {}
        self._last_try = 0.0
        self._x = None
        try:
            self._open(loader)
        except Exception as e:
            log.info("no tray icon: %s", e)
            self.reason = str(e)
            self._x = None

    # ---------------------------------------------------------------- setup
    def _open(self, loader):
        import os
        if os.environ.get("EVERLAST_NO_TRAY", "").strip() not in ("", "0"):
            raise OSError("turned off by EVERLAST_NO_TRAY")
        if not os.environ.get("DISPLAY"):
            raise OSError("no X display")
        c = ctypes
        x = loader("X11")
        x.XOpenDisplay.restype = c.c_void_p
        x.XOpenDisplay.argtypes = [c.c_char_p]
        x.XDefaultRootWindow.restype = c.c_ulong
        x.XDefaultRootWindow.argtypes = [c.c_void_p]
        x.XInternAtom.restype = c.c_ulong
        x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
        x.XGetSelectionOwner.restype = c.c_ulong
        x.XGetSelectionOwner.argtypes = [c.c_void_p, c.c_ulong]
        x.XCreateSimpleWindow.restype = c.c_ulong
        x.XCreateSimpleWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint,
                                          c.c_uint, c.c_uint, c.c_ulong, c.c_ulong]
        x.XSelectInput.argtypes = [c.c_void_p, c.c_ulong, c.c_long]
        x.XMapWindow.argtypes = [c.c_void_p, c.c_ulong]
        x.XDestroyWindow.argtypes = [c.c_void_p, c.c_ulong]
        x.XFlush.argtypes = [c.c_void_p]
        x.XPending.argtypes = [c.c_void_p]
        x.XNextEvent.argtypes = [c.c_void_p, c.POINTER(_XEvent)]
        x.XSendEvent.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_long, c.POINTER(_XEvent)]
        x.XChangeProperty.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_ulong, c.c_int,
                                      c.c_int, c.c_void_p, c.c_int]
        x.XStoreName.argtypes = [c.c_void_p, c.c_ulong, c.c_char_p]
        x.XCreateGC.restype = c.c_void_p
        x.XCreateGC.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_void_p]
        x.XDefaultVisual.restype = c.c_void_p
        x.XDefaultVisual.argtypes = [c.c_void_p, c.c_int]
        x.XDefaultDepth.argtypes = [c.c_void_p, c.c_int]
        x.XDefaultScreen.argtypes = [c.c_void_p]
        x.XCreateImage.restype = c.POINTER(_XImage)
        x.XCreateImage.argtypes = [c.c_void_p, c.c_void_p, c.c_uint, c.c_int, c.c_int,
                                   c.c_void_p, c.c_uint, c.c_uint, c.c_int, c.c_int]
        x.XPutImage.argtypes = [c.c_void_p, c.c_ulong, c.c_void_p, c.c_void_p, c.c_int, c.c_int,
                                c.c_int, c.c_int, c.c_uint, c.c_uint]
        x.XClearWindow.argtypes = [c.c_void_p, c.c_ulong]
        x.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong),
                                 c.POINTER(c.c_ulong), c.POINTER(c.POINTER(c.c_ulong)),
                                 c.POINTER(c.c_uint)]
        x.XFree.argtypes = [c.c_void_p]
        x.XCloseDisplay.argtypes = [c.c_void_p]
        dpy = x.XOpenDisplay(None)
        if not dpy:
            raise OSError("cannot open the X display")
        xguard.watch(x, dpy)
        self._x, self._dpy = x, dpy
        self._root = x.XDefaultRootWindow(dpy)
        scr = x.XDefaultScreen(dpy)
        self._depth = x.XDefaultDepth(dpy, scr)
        vis = x.XDefaultVisual(dpy, scr)
        self._vis = vis
        masks = (c.c_ulong * 3).from_address(vis + 24)      # red, green, blue masks
        self._masks = [int(m) for m in masks]
        if self._depth < 24 or not all(self._masks):
            raise OSError("needs a 24-bit TrueColor display")
        self._sel = x.XInternAtom(dpy, b"_NET_SYSTEM_TRAY_S%d" % scr, 0)
        self._opcode = x.XInternAtom(dpy, b"_NET_SYSTEM_TRAY_OPCODE", 0)
        self._win = x.XCreateSimpleWindow(dpy, self._root, 0, 0, self.size, self.size, 0, 0,
                                          0x161d28)
        x.XSelectInput(dpy, self._win,
                       EXPOSURE_MASK | BUTTON_PRESS_MASK | STRUCTURE_NOTIFY_MASK)
        x.XStoreName(dpy, self._win, self.title.encode())
        # _XEMBED_INFO: version 0, flags 1 (= mapped)
        info = x.XInternAtom(dpy, b"_XEMBED_INFO", 0)
        data = (ctypes.c_ulong * 2)(0, 1)
        x.XChangeProperty(dpy, self._win, info, info, 32, 0, ctypes.cast(data, c.c_void_p), 2)
        self._gc = x.XCreateGC(dpy, self._win, 0, None)
        self.available = True
        self._dock()

    # ---------------------------------------------------------------- docking
    def _parent(self):
        c = ctypes
        root, parent, kids, n = c.c_ulong(), c.c_ulong(), c.POINTER(c.c_ulong)(), c.c_uint()
        if not self._x.XQueryTree(self._dpy, self._win, c.byref(root), c.byref(parent),
                                  c.byref(kids), c.byref(n)):
            return None
        if kids:
            self._x.XFree(kids)
        return int(parent.value)

    def _dock(self):
        """Ask the panel to adopt our window (does nothing without a tray)."""
        x = self._x
        owner = x.XGetSelectionOwner(self._dpy, self._sel)
        if not owner:
            return False
        ev = _XEvent()
        m = ev.msg
        m.type, m.window, m.message_type, m.format = CLIENT_MESSAGE, owner, self._opcode, 32
        m.data[0], m.data[1], m.data[2] = 0, 0, self._win     # CurrentTime, REQUEST_DOCK, us
        x.XSendEvent(self._dpy, owner, 0, 0, ctypes.byref(ev))
        # Never map the window ourselves: the panel adopts and maps it (_XEMBED_INFO says
        # "mapped"). Mapping it first makes the window manager briefly manage it as an
        # ordinary top-level window, and Muffin can crash when that window then
        # vanishes into the tray.
        x.XFlush(self._dpy)
        return True

    def _update_docked(self):
        parent = self._parent()
        self.docked = parent is not None and parent not in (self._root, 0)

    # ---------------------------------------------------------------- drawing
    def _picture(self, size):
        """size x size pixels (r, g, b), blended over the background colour."""
        if size not in self._pix:
            src = best_size(size)
            f = self._dir / f"everlast-{src}.png"
            w, h, px = decode_png(f.read_bytes())
            self._pix[size] = flatten(scale_rgba(w, h, px, size))
        return self._pix[size]

    def _pack(self, rgb):
        r, g, b = rgb
        out = 0
        for v, mask in zip((r, g, b), self._masks):
            shift = (mask & -mask).bit_length() - 1
            width = bin(mask).count("1")
            out |= ((v >> (8 - width)) << shift) & mask if width <= 8 else 0
        return out

    def draw(self):
        if not self.available:
            return
        try:
            size = max(8, min(256, int(self.size)))
            img = self._img.get(size)
            if img is None:
                pix = [self._pack(p) for p in self._picture(size)]
                buf = (ctypes.c_uint32 * (size * size))(*pix)
                image = self._x.XCreateImage(self._dpy, self._vis, self._depth, ZPIXMAP, 0,
                                             ctypes.cast(buf, ctypes.c_void_p), size, size, 32, 0)
                img = self._img[size] = (image, buf)
            self._x.XPutImage(self._dpy, self._win, self._gc, img[0], 0, 0, 0, 0, size, size)
            self._x.XFlush(self._dpy)
        except Exception:
            log.exception("tray icon draw failed")

    # ---------------------------------------------------------------- events
    def poll(self):
        """Handle pending X events and keep trying to dock. Call this a few times a second."""
        if not self.available:
            return
        x = self._x
        try:
            ev = _XEvent()
            while x.XPending(self._dpy):
                x.XNextEvent(self._dpy, ctypes.byref(ev))
                self._event(ev)
            self._update_docked()
            now = time.monotonic()
            if not self.docked and now - self._last_try > 3.0:
                self._last_try = now
                self._dock()
        except Exception:
            log.exception("tray poll failed")

    def _event(self, ev):
        kind = ev.type
        raw = bytes(ctypes.string_at(ctypes.addressof(ev), 96))
        if kind == EXPOSE:
            self.draw()
        elif kind == CONFIGURE_NOTIFY:
            # XConfigureEvent: width at offset 56, height at 60
            w, h = struct.unpack_from("ii", raw, 56)
            if w > 0 and h > 0 and min(w, h) != self.size:
                self.size = min(w, h)
                self.draw()
        elif kind == BUTTON_PRESS:
            xr, yr = struct.unpack_from("ii", raw, 72)
            button = struct.unpack_from("I", raw, 84)[0]
            if button == 1 and self.on_click:
                self.on_click()
            elif button in (2, 3) and self.on_menu:
                self.on_menu(xr, yr)
        elif kind in (REPARENT_NOTIFY, DESTROY_NOTIFY):
            self._update_docked()
            if kind == DESTROY_NOTIFY:          # the panel went away and took us with it
                self.docked = False
                self._rebuild()

    def _rebuild(self):
        """Our window was destroyed with the panel: make a new one for the next tray."""
        try:
            x, c = self._x, ctypes
            self._win = x.XCreateSimpleWindow(self._dpy, self._root, 0, 0, self.size, self.size,
                                              0, 0, 0x161d28)
            x.XSelectInput(self._dpy, self._win,
                           EXPOSURE_MASK | BUTTON_PRESS_MASK | STRUCTURE_NOTIFY_MASK)
            x.XStoreName(self._dpy, self._win, self.title.encode())
            info = x.XInternAtom(self._dpy, b"_XEMBED_INFO", 0)
            data = (c.c_ulong * 2)(0, 1)
            x.XChangeProperty(self._dpy, self._win, info, info, 32, 0, c.cast(data, c.c_void_p), 2)
            self._gc = x.XCreateGC(self._dpy, self._win, 0, None)
            self._last_try = 0.0
        except Exception:
            log.exception("could not rebuild the tray window")

    def close(self):
        if self._x is None or not self.available:
            return
        try:
            self._x.XDestroyWindow(self._dpy, self._win)
            self._x.XFlush(self._dpy)
            self._x.XCloseDisplay(self._dpy)
        except Exception:
            pass
        self.available = self.docked = False
