"""System tray: PNG decoding, scaling, and docking against a fake tray panel.

The docking test runs in its own process under an X display (skipped without one)."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ICONS = os.path.join(ROOT, "eqtool_native", "icons")
sys.path.insert(0, ROOT)

from eqtool_native.ui import tray  # noqa: E402


class PictureTests(unittest.TestCase):
    def test_decode_all_icons(self):
        for s in tray.SIZES:
            w, h, px = tray.decode_png(open(os.path.join(ICONS, f"everlast-{s}.png"), "rb").read())
            self.assertEqual((w, h, len(px)), (s, s, s * s * 4))
            self.assertTrue(any(px[i] != px[0] for i in range(0, len(px), 4)))   # not blank

    def test_corner_transparent_centre_opaque(self):
        w, h, px = tray.decode_png(open(os.path.join(ICONS, "everlast-64.png"), "rb").read())
        self.assertEqual(px[3], 0)
        self.assertEqual(px[((32 * w) + 32) * 4 + 3], 255)

    def test_scale_and_flatten(self):
        w, h, px = tray.decode_png(open(os.path.join(ICONS, "everlast-48.png"), "rb").read())
        out = tray.scale_rgba(w, h, px, 20)
        self.assertEqual(len(out), 20 * 20 * 4)
        flat = tray.flatten(out)
        self.assertEqual(len(flat), 400)
        self.assertEqual(flat[0], tray.BACKGROUND)       # transparent corner -> background

    def test_best_size(self):
        self.assertEqual(tray.best_size(22), 22)
        self.assertEqual(tray.best_size(23), 24)
        self.assertEqual(tray.best_size(999), 256)

    def test_bad_png(self):
        with self.assertRaises(ValueError):
            tray.decode_png(b"nope")


FAKE_PANEL = '''
import ctypes, os, sys, time
sys.path.insert(0, %(root)r)
from eqtool_native.ui import tray
from eqtool_native.ui.xinspect import _lib
c = ctypes
x = _lib("X11")
x.XOpenDisplay.restype = c.c_void_p; x.XOpenDisplay.argtypes = [c.c_char_p]
x.XDefaultRootWindow.restype = c.c_ulong; x.XDefaultRootWindow.argtypes = [c.c_void_p]
x.XInternAtom.restype = c.c_ulong; x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
x.XCreateSimpleWindow.restype = c.c_ulong
x.XCreateSimpleWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint, c.c_uint, c.c_ulong, c.c_ulong]
x.XSetSelectionOwner.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_ulong]
x.XSelectInput.argtypes = [c.c_void_p, c.c_ulong, c.c_long]
x.XPending.argtypes = [c.c_void_p]
x.XNextEvent.argtypes = [c.c_void_p, c.POINTER(tray._XEvent)]
x.XReparentWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_int, c.c_int]
x.XMapWindow.argtypes = [c.c_void_p, c.c_ulong]
x.XFlush.argtypes = [c.c_void_p]
x.XSendEvent.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_long, c.POINTER(tray._XEvent)]
x.XGetWindowAttributes
d = x.XOpenDisplay(None)
root = x.XDefaultRootWindow(d)
panel = x.XCreateSimpleWindow(d, root, 0, 0, 200, 30, 0, 0, 0)
x.XSetSelectionOwner(d, x.XInternAtom(d, b"_NET_SYSTEM_TRAY_S0", 0), panel, 0)
x.XSelectInput(d, panel, 1 << 19)   # SubstructureNotify
x.XMapWindow(d, panel); x.XFlush(d)
print("READY", flush=True)
op = x.XInternAtom(d, b"_NET_SYSTEM_TRAY_OPCODE", 0)
docked = None
end = time.time() + 12
ev = tray._XEvent()
while time.time() < end:
    while x.XPending(d):
        x.XNextEvent(d, c.byref(ev))
        if ev.type == tray.CLIENT_MESSAGE and ev.msg.message_type == op and not docked:
            docked = ev.msg.data[2]
            x.XReparentWindow(d, docked, panel, 0, 0)
            x.XMapWindow(d, docked); x.XFlush(d)
            print("DOCKED", flush=True)
            # a left click, then a right click, sent to the tray window
            for button in (1, 3):
                e = tray._XEvent()
                e.type = tray.BUTTON_PRESS
                raw = (c.c_char * 192).from_address(c.addressof(e))
                import struct
                struct.pack_into("Q", raw, 32, docked)
                struct.pack_into("ii", raw, 72, 321, 654)
                struct.pack_into("I", raw, 84, button)
                x.XSendEvent(d, docked, 0, tray.BUTTON_PRESS_MASK, c.byref(e))
            x.XFlush(d)
    time.sleep(0.05)
'''

APP = '''
import os, sys, time
sys.path.insert(0, %(root)r)
from eqtool_native.ui import tray
log = []
t = tray.TrayIcon(%(icons)r, on_click=lambda: log.append("click"),
                  on_menu=lambda x, y: log.append(("menu", x, y)))
print("available", t.available, t.reason, flush=True)
end = time.time() + 6
while time.time() < end and len(log) < 2:
    t.poll(); time.sleep(0.05)
print("docked", t.docked, flush=True)
print("events", log, flush=True)
t.close()
'''


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display (xvfb-run)")
class DockTests(unittest.TestCase):
    def test_dock_and_clicks(self):
        env = dict(os.environ)
        panel = subprocess.Popen([sys.executable, "-c", FAKE_PANEL % {"root": ROOT}], env=env,
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(panel.stdout.readline().strip(), "READY")
            r = subprocess.run([sys.executable, "-c", APP % {"root": ROOT, "icons": ICONS}],
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertIn("available True", r.stdout, r.stderr)
            self.assertIn("docked True", r.stdout, r.stdout + r.stderr)
            self.assertIn("'click'", r.stdout)
            self.assertIn("('menu', 321, 654)", r.stdout)
        finally:
            panel.kill()

    def test_no_tray_is_not_docked(self):
        code = textwrap.dedent(f'''
            import sys; sys.path.insert(0, {ROOT!r})
            from eqtool_native.ui import tray
            t = tray.TrayIcon({ICONS!r})
            t.poll()
            print(t.available, t.docked)
            t.close()
        ''')
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.split(), ["True", "False"], r.stderr)


if __name__ == "__main__":
    unittest.main()
