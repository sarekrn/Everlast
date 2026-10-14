"""Click-through against a real X server. Run under a desktop session or
`xvfb-run python3 -m unittest tests.test_clickthrough`; skipped without X."""
import ctypes
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from eqtool_native.ui.clickthrough import ClickThrough  # noqa: E402


class _Root:
    class tk:
        @staticmethod
        def call(*a):
            return "x11"


class _Win:
    def __init__(self, wid):
        self.wid = wid

    def update_idletasks(self):
        pass

    def winfo_exists(self):
        return True

    def wm_frame(self):
        return hex(self.wid)

    def winfo_id(self):
        return self.wid


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display")
class ClickThroughX11Test(unittest.TestCase):
    def test_clicks_reach_the_window_below(self):
        ct = ClickThrough(_Root())
        self.assertTrue(ct.available, ct.reason)
        x, dpy = ct._x, ct._dpy
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XCreateSimpleWindow.restype = ctypes.c_ulong
        x.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                          ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
        x.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong)]
        root = x.XDefaultRootWindow(dpy)
        game = x.XCreateSimpleWindow(dpy, root, 0, 0, 300, 300, 0, 0, 0)
        wrapper = x.XCreateSimpleWindow(dpy, root, 50, 50, 100, 100, 0, 0, 0)
        canvas = x.XCreateSimpleWindow(dpy, wrapper, 0, 0, 100, 100, 0, 0, 0)
        for w in (game, wrapper, canvas):
            x.XMapWindow(dpy, w)
        x.XSync(dpy, 0)

        def deepest(px, py):
            cur, dx, dy, child = root, ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
            while True:
                x.XTranslateCoordinates(dpy, root, cur, px, py, ctypes.byref(dx),
                                        ctypes.byref(dy), ctypes.byref(child))
                if not child.value:
                    return cur
                cur = child.value

        self.assertEqual(deepest(100, 100), canvas)
        self.assertTrue(ct.set(_Win(wrapper), True))
        x.XSync(dpy, 0)
        self.assertEqual(deepest(100, 100), game)
        self.assertTrue(ct.set(_Win(wrapper), False))
        x.XSync(dpy, 0)
        self.assertEqual(deepest(100, 100), canvas)


class ClickThroughFallbackTest(unittest.TestCase):
    def test_unknown_window_system_is_harmless(self):
        class R:
            class tk:
                @staticmethod
                def call(*a):
                    return "aqua"
        ct = ClickThrough(R())
        self.assertFalse(ct.available)
        self.assertIn("aqua", ct.reason)
        self.assertFalse(ct.set(object(), True))

    def test_missing_libraries_are_harmless(self):
        def loader(name):
            raise OSError(f"lib{name} not found")
        ct = ClickThrough(_Root(), loader=loader)
        self.assertFalse(ct.available)
        self.assertIn("not found", ct.reason)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display")
class DecorationsX11Test(unittest.TestCase):
    def test_motif_hints_written_and_removed(self):
        from eqtool_native.ui.clickthrough import Decorations
        dec = Decorations(_Root())
        self.assertTrue(dec.available)
        x, dpy = dec._x, dec._dpy
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XCreateSimpleWindow.restype = ctypes.c_ulong
        x.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                          ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
        x.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        wid = x.XCreateSimpleWindow(dpy, x.XDefaultRootWindow(dpy), 0, 0, 50, 50, 0, 0, 0)

        def hints():
            t, f, n, after, data = (ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(),
                                    ctypes.c_ulong(), ctypes.c_void_p())
            x.XSync(dpy, 0)
            x.XGetWindowProperty(dpy, wid, dec._atom, 0, 5, 0, 0, ctypes.byref(t), ctypes.byref(f),
                                 ctypes.byref(n), ctypes.byref(after), ctypes.byref(data))
            if not n.value:
                return None
            arr = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            return [arr[i] for i in range(n.value)]

        self.assertIsNone(hints())
        self.assertTrue(dec.set(_Win(wid), decorated=False))
        self.assertEqual(hints(), [2, 0, 0, 0, 0])   # decorations flag set, decorations = none
        self.assertTrue(dec.set(_Win(wid), decorated=True))
        self.assertIsNone(hints())


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display")
class OpacityX11Test(unittest.TestCase):
    def test_opacity_hint(self):
        from eqtool_native.ui.clickthrough import Decorations
        dec = Decorations(_Root())
        x, dpy = dec._x, dec._dpy
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XCreateSimpleWindow.restype = ctypes.c_ulong
        x.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                          ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
        x.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        wid = x.XCreateSimpleWindow(dpy, x.XDefaultRootWindow(dpy), 0, 0, 50, 50, 0, 0, 0)

        def opacity():
            t, f, n, after, data = (ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(),
                                    ctypes.c_ulong(), ctypes.c_void_p())
            x.XSync(dpy, 0)
            x.XGetWindowProperty(dpy, wid, dec._opacity_atom, 0, 1, 0, 0, ctypes.byref(t),
                                 ctypes.byref(f), ctypes.byref(n), ctypes.byref(after),
                                 ctypes.byref(data))
            if not n.value:
                return None
            raw = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0] & 0xFFFFFFFF  # Xlib sign-extends
            return round(raw / 0xFFFFFFFF, 3)

        self.assertTrue(dec.set_opacity(_Win(wid), 0.6))
        self.assertEqual(opacity(), 0.6)
        dec.set_opacity(_Win(wid), 1.0)
        self.assertIsNone(opacity())


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display")
class ShapeX11Test(unittest.TestCase):
    """set_shape must leave only the given rectangles visible, on screen."""

    def test_only_the_rectangles_are_visible(self):
        ct = ClickThrough(_Root())
        self.assertTrue(ct.shape_available)
        x, dpy = ct._x, ct._dpy

        class XImage(ctypes.Structure):
            pass
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XCreateSimpleWindow.restype = ctypes.c_ulong
        x.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                          ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
        x.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XMapSubwindows.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XGetImage.restype = ctypes.POINTER(XImage)
        x.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
        x.XGetPixel.restype = ctypes.c_ulong
        x.XGetPixel.argtypes = [ctypes.POINTER(XImage), ctypes.c_int, ctypes.c_int]
        root = x.XDefaultRootWindow(dpy)
        RED, BLUE = 0xFF0000, 0x0000FF
        outer = x.XCreateSimpleWindow(dpy, root, 100, 100, 200, 100, 0, 0, BLUE)
        x.XCreateSimpleWindow(dpy, outer, 0, 0, 200, 100, 0, 0, RED)   # a widget covering it all
        x.XMapWindow(dpy, outer)
        x.XMapSubwindows(dpy, outer)
        x.XSync(dpy, 0)

        def pixel(px, py):
            return x.XGetPixel(x.XGetImage(dpy, root, px, py, 1, 1, 0xFFFFFFFF, 2), 0, 0)

        desktop = pixel(5, 5)
        self.assertEqual(pixel(250, 180), RED)                       # before: all visible
        self.assertEqual(ct.set_shape(_Win(outer), [(10, 10, 50, 20), (100, 60, 40, 20)]), outer)
        x.XSync(dpy, 0)
        self.assertEqual(pixel(130, 120), RED)                       # inside rectangle 1
        self.assertEqual(pixel(220, 170), RED)                       # inside rectangle 2
        self.assertEqual(pixel(250, 110), desktop)                   # cut away (child too)
        self.assertEqual(pixel(105, 195), desktop)
        self.assertEqual(ct.set_shape(_Win(outer), []), outer)       # nothing to show
        x.XSync(dpy, 0)
        self.assertEqual(pixel(130, 120), desktop)
        self.assertEqual(ct.set_shape(_Win(outer), None), outer)     # back to normal
        x.XSync(dpy, 0)
        self.assertEqual(pixel(250, 180), RED)

    def test_unmapped_window_is_harmless(self):
        ct = ClickThrough(_Root())

        class Unmapped(_Win):
            def wm_frame(self):
                return ""
        w = Unmapped(0)
        self.assertEqual(ct.set_shape(w, [(0, 0, 5, 5)]), 0)
        self.assertFalse(ct.set(w, True))


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display")
class GlyphShapeTests(unittest.TestCase):
    def test_only_non_background_pixels_are_kept(self):
        import subprocess, sys
        probe = os.path.join(os.path.dirname(__file__), "glyph_probe.py")
        out = subprocess.run([sys.executable, probe], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "[(10, 5, 30, 20), (100, 30, 50, 10)]", out.stderr)
