"""The diagnostics' X11 inspector against a real X server (skipped without one)."""
import ctypes
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from eqtool_native.ui.clickthrough import ClickThrough, Decorations  # noqa: E402
from eqtool_native.ui.xinspect import SHAPE_BOUNDING, SHAPE_INPUT, XInspector, conclude  # noqa: E402


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
class XInspectorTest(unittest.TestCase):
    def setUp(self):
        self.xi = XInspector()
        self.assertTrue(self.xi.available, self.xi.reason)
        self.ct, self.dec = ClickThrough(_Root()), Decorations(_Root())
        x, dpy = self.xi._x, self.xi._dpy
        x.XCreateSimpleWindow.restype = ctypes.c_ulong
        x.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                          ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
        x.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XStoreName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p]
        x.XSetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                         ctypes.c_ulong]
        self.x, self.dpy = x, dpy

    def sync(self):
        """Requests from different X connections aren't ordered; sync both."""
        self.ct._x.XSync(self.ct._dpy, 0)
        self.dec._x.XSync(self.dec._dpy, 0)
        self.x.XSync(self.dpy, 0)

    def make(self, geom, name=None):
        w = self.x.XCreateSimpleWindow(self.dpy, self.xi.root, *geom, 0, 0, 0)
        if name:
            self.x.XStoreName(self.dpy, w, name.encode())
        self.x.XMapWindow(self.dpy, w)
        self.x.XSync(self.dpy, 0)
        return w

    def test_reads_back_shapes_opacity_and_attributes(self):
        w = self.make((10, 10, 120, 80, ))
        self.assertIsNone(self.xi.opacity(w))
        self.assertEqual(self.xi.rects(w, SHAPE_INPUT), [(0, 0, 120, 80)])   # unshaped: whole window
        self.assertTrue(self.dec.set_opacity(_Win(w), 0.0))
        self.sync()
        self.assertEqual(self.xi.opacity(w), 0.0)       # 0% really reaches the window
        self.dec.set_opacity(_Win(w), 0.5)
        self.sync()
        self.assertAlmostEqual(self.xi.opacity(w), 0.5, places=2)
        self.assertTrue(self.ct.set(_Win(w), True))
        self.sync()
        self.assertEqual(self.xi.rects(w, SHAPE_INPUT), [])             # clicks blocked from nothing
        self.ct.set_shape(_Win(w), [(5, 5, 30, 20)])
        self.sync()
        self.assertEqual(self.xi.rects(w, SHAPE_BOUNDING), [(5, 5, 30, 20)])
        a = self.xi.attrs(w)
        self.assertEqual((a.width, a.height, a.map_state), (120, 80, 2))
        self.assertEqual(self.xi.tree(w)[0], self.xi.root)

    def test_pointer_probe_sees_through_a_click_through_window(self):
        game = self.make((0, 0, 400, 300), name="EverQuest")
        overlay = self.make((50, 50, 200, 100), name="Buff Timers")
        chain = lambda: self.xi.chain_at(100, 100)                       # noqa: E731
        self.assertEqual(chain()[0], overlay)
        self.assertEqual(self.xi.toplevel_name(chain()[0]), "Buff Timers")
        self.ct.set(_Win(overlay), True)
        self.sync()
        self.assertEqual(chain()[0], game)
        self.assertEqual(self.xi.toplevel_name(chain()[0]), "EverQuest")
        self.ct.set(_Win(overlay), False)
        self.sync()
        self.assertEqual(chain()[0], overlay)

    def test_click_through_with_a_clickable_strip(self):
        """The play-mode scroll strip: clicks land on it, everywhere else they go through."""
        game = self.make((0, 0, 400, 300), name="EverQuest")
        overlay = self.make((50, 50, 200, 100), name="Buff Timers")      # covers x 50..250, y 50..150
        self.assertTrue(self.ct.set_input(_Win(overlay), [(180, 0, 20, 100)]))   # a strip: x 230..250
        self.sync()
        self.assertEqual(self.xi.rects(overlay, SHAPE_INPUT), [(180, 0, 20, 100)])
        self.assertEqual(self.xi.chain_at(240, 100)[0], overlay)         # on the strip: the overlay gets it
        self.assertEqual(self.xi.chain_at(100, 100)[0], game)            # on the bars: straight through
        self.assertEqual(self.xi.chain_at(229, 100)[0], game)            # one pixel left of the strip
        self.assertEqual(self.xi.chain_at(251, 100)[0], game)            # one pixel right of the window
        self.assertTrue(self.ct.set_input(_Win(overlay), [(0, 0, 10, 10), (190, 90, 10, 10)]))
        self.sync()
        self.assertEqual(self.xi.chain_at(55, 55)[0], overlay)           # several rectangles work
        self.assertEqual(self.xi.chain_at(245, 145)[0], overlay)
        self.assertEqual(self.xi.chain_at(150, 100)[0], game)
        self.ct.set(_Win(overlay), True)                                # fully click-through again
        self.sync()
        self.assertEqual(self.xi.chain_at(55, 55)[0], game)

    def tk_stack(self, inner_at=(0, 0)):
        """What Tk really builds for an override-redirect toplevel on X11:
        root -> WRAPPER -> INNER (Tk's own window) -> canvas. Tk reports the INNER id
        for both `wm frame` and `winfo id`; only the wrapper is a child of the root."""
        x, dpy = self.x, self.dpy
        game = self.make((0, 0, 500, 400), name="EverQuest")
        wrapper = x.XCreateSimpleWindow(dpy, self.xi.root, 100, 100, 210, 130, 0, 0, 0)
        inner = x.XCreateSimpleWindow(dpy, wrapper, inner_at[0], inner_at[1], 200, 120, 0, 0, 0)
        canvas = x.XCreateSimpleWindow(dpy, inner, 0, 0, 200, 120, 0, 0, 0)
        x.XMapWindow(dpy, wrapper)
        x.XMapSubwindows(dpy, wrapper)
        x.XMapSubwindows(dpy, inner)
        x.XSync(dpy, 0)
        return game, wrapper, inner, canvas

    def test_click_through_reaches_the_outermost_window_of_a_tk_toplevel(self):
        """The bug: click-through was applied to Tk's inner window only, and the wrapper
        around it (the window the screen and the pointer actually deal with) stayed fully
        clickable, so every click was swallowed by it and never reached the game."""
        game, wrapper, inner, canvas = self.tk_stack()
        win = _Win(inner)                                         # wm_frame() == winfo_id() == inner
        self.assertEqual(self.ct.window_id(win), wrapper)         # the real top-level is found
        self.assertEqual(self.xi.chain_at(150, 150)[0], wrapper)  # before: the overlay gets the click
        self.assertTrue(self.ct.set(win, True))
        self.sync()
        self.assertEqual(self.xi.chain_at(150, 150)[0], game)     # after: it goes through to the game
        self.assertEqual(self.xi.rects(wrapper, SHAPE_INPUT), [])
        self.assertTrue(self.ct.set(win, False))
        self.sync()
        self.assertEqual(self.xi.chain_at(150, 150)[0], wrapper)  # and back

    def test_old_behaviour_would_have_failed(self):
        """Documents the bug: an empty input region on the inner window alone swallows clicks."""
        game, wrapper, inner, canvas = self.tk_stack()
        self.ct._xext.XShapeCombineRectangles(self.ct._dpy, inner, SHAPE_INPUT, 0, 0, None, 0, 0, 0)
        self.sync()
        self.assertEqual(self.xi.chain_at(150, 150)[0], wrapper)  # eaten by the wrapper, not the game

    def test_shape_and_strip_land_on_the_wrapper_with_the_right_offsets(self):
        game, wrapper, inner, canvas = self.tk_stack(inner_at=(3, 5))   # inner sits 3,5 inside the wrapper
        win = _Win(inner)
        self.assertEqual(self.ct.set_shape(win, [(10, 10, 50, 20)]), wrapper)
        self.sync()
        self.assertEqual(self.xi.rects(wrapper, SHAPE_BOUNDING), [(13, 15, 50, 20)])     # shifted by 3,5
        self.assertEqual(self.xi.rects(inner, SHAPE_BOUNDING), [(10, 10, 50, 20)])       # its own coordinates
        self.assertTrue(self.ct.set_input(win, [(10, 10, 20, 20)]))
        self.sync()
        self.assertEqual(self.xi.rects(wrapper, SHAPE_INPUT), [(13, 15, 20, 20)])
        self.assertEqual(self.xi.chain_at(100 + 3 + 15, 100 + 5 + 15)[0], wrapper)       # on the strip
        self.assertEqual(self.xi.chain_at(100 + 3 + 60, 100 + 5 + 60)[0], game)          # elsewhere: through
        self.assertEqual(self.ct.set_shape(win, None), wrapper)                          # restore
        self.sync()
        self.assertEqual(self.xi.rects(wrapper, SHAPE_BOUNDING), [(0, 0, 210, 130)])

    def test_opacity_and_hints_go_on_the_outermost_window(self):
        game, wrapper, inner, canvas = self.tk_stack()
        win = _Win(inner)
        self.assertTrue(self.dec.set_opacity(win, 0.4))
        self.sync()
        self.assertAlmostEqual(self.xi.opacity(wrapper), 0.4, places=2)    # what the compositor reads
        self.assertIsNone(self.xi.opacity(inner))

    def test_stacking_and_compositor_detection(self):
        a = self.make((0, 0, 100, 100), name="below")
        b = self.make((50, 50, 100, 100), name="above")
        self.assertIn("above", [n for n, _, _ in self.xi.above(a)])
        self.assertEqual(self.xi.above(b), [])
        self.assertFalse(self.xi.compositor_running())                   # Xvfb has none
        owner = self.make((0, 0, 1, 1))
        self.x.XSetSelectionOwner(self.dpy, self.xi.atom("_NET_WM_CM_S0"), owner, 0)
        self.x.XSync(self.dpy, 0)
        self.assertTrue(self.xi.compositor_running())
        self.x.XSetSelectionOwner(self.dpy, self.xi.atom("_NET_WM_CM_S0"), 0, 0)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display")
class CompositingHintsTest(unittest.TestCase):
    def test_finds_fullscreen_and_bypass_windows(self):
        xi = XInspector()
        self.assertTrue(xi.available, xi.reason)
        x, dpy, c = xi._x, xi._dpy, ctypes
        x.XCreateSimpleWindow.restype = c.c_ulong
        x.XCreateSimpleWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint,
                                          c.c_uint, c.c_ulong, c.c_ulong]
        x.XMapWindow.argtypes = [c.c_void_p, c.c_ulong]
        x.XStoreName.argtypes = [c.c_void_p, c.c_ulong, c.c_char_p]
        x.XSync.argtypes = [c.c_void_p, c.c_int]
        x.XChangeProperty.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_ulong, c.c_int, c.c_int,
                                      c.c_void_p, c.c_int]

        def window(name):
            w = x.XCreateSimpleWindow(dpy, xi.root, 0, 0, 80, 60, 0, 0, 0)
            x.XStoreName(dpy, w, name.encode())
            x.XMapWindow(dpy, w)
            return w

        def card(w, prop, kind, vals):
            arr = (c.c_ulong * len(vals))(*vals)
            x.XChangeProperty(dpy, w, xi.atom(prop), xi.atom(kind), 32, 0, c.cast(arr, c.c_void_p), len(vals))

        game, plain, fs = window("Game"), window("Plain"), window("FullscreenOnly")
        polite = window("Polite")
        card(polite, "_NET_WM_BYPASS_COMPOSITOR", "CARDINAL", [2])     # what Chrome and Discord set
        card(game, "_NET_WM_BYPASS_COMPOSITOR", "CARDINAL", [1])
        card(fs, "_NET_WM_STATE", "ATOM", [xi.atom("_NET_WM_STATE_FULLSCREEN")])
        x.XSync(dpy, 0)
        got = {n: (b, f) for n, b, f in xi.compositing_hints()}
        self.assertEqual(got.get("Game"), (1, False))                  # asks to bypass compositing
        self.assertNotIn("Polite", got)                                # value 2 asks NOT to be bypassed
        self.assertEqual(got.get("FullscreenOnly"), (None, True))      # fullscreen, no bypass request
        self.assertNotIn("Plain", got)                                 # an ordinary window is not listed


class ConcludeTests(unittest.TestCase):
    Z = {"A": 0, "B": 0, "C": 0}

    def test_readings(self):
        self.assertIn("No clicks recorded yet", conclude(self.Z, {"A": 0, "B": 0}, 0))
        ok = conclude({"A": 2, "B": 1, "C": 1}, {"A": 0, "B": 0}, 0)
        self.assertIn("A: click-through WORKS", ok)
        self.assertIn("B: click-through WORKS", ok)
        bad = conclude({"A": 0, "B": 0, "C": 1}, {"A": 3, "B": 1}, 0)
        self.assertIn("A: BLOCKED", bad)
        self.assertIn("B: BLOCKED", bad)
        mixed = conclude({"A": 1, "B": 0, "C": 1}, {"A": 1, "B": 0}, 0)
        self.assertIn("A: mixed (1 passed, 1 caught)", mixed)

    def test_border_clicks_are_not_a_pass(self):
        # exactly the result a user reported: only the grey border was clicked
        r = conclude({"A": 0, "B": 0, "C": 1}, {"A": 0, "B": 0}, 0, {"A": 9, "B": 9})
        self.assertNotIn("WORKS", r)
        self.assertIn("A: 9 click(s) hit the grey border", r)
        self.assertIn("those do not test it", r)
        self.assertIn("the click vanished", r)                 # and the red-square clicks went nowhere

    def test_hover_hide_test_d(self):
        T = {"A": 0, "B": 0, "D": 0, "C": 1}
        Bl = {"A": 0, "B": 0, "D": 0}
        ok = conclude({**T, "D": 2}, Bl, 0, {"A": 0, "B": 0, "D": 0})
        self.assertIn("D (hover-hide): WORKS", ok)
        none = conclude(T, Bl, 0, {"A": 0, "B": 0, "D": 0})
        self.assertIn("D (hover-hide): nothing recorded.", none)
        self.assertIn("pointer line", none)                     # tells the user where to look next
        self.assertNotIn("XWayland", none.split("D (hover-hide)")[1])   # D's advice is about hover-hide
        caught = conclude(T, {**Bl, "D": 3}, 0)
        self.assertIn("D (hover-hide): BLOCKED - the overlay caught the click before it could hide.", caught)
        self.assertIn("move the mouse onto the red square", conclude({"A": 0, "B": 0, "D": 0, "C": 0}, Bl, 0))

    def test_nothing_recorded_is_not_claimed_as_a_failure(self):
        r = conclude({"A": 0, "B": 0, "C": 1}, {"A": 0, "B": 0}, 0)
        self.assertIn("Control button C works", r)
        self.assertIn("A: nothing recorded. If you clicked INSIDE the red square", r)   # hedged, not asserted
        self.assertNotIn("BLOCKED", r)
        bg = conclude({"A": 0, "B": 0, "C": 1}, {"A": 0, "B": 0}, 2)
        self.assertIn("passed through the overlay but the overlay is not over the button", bg)
        dead = conclude(self.Z, {"A": 0, "B": 0}, 1)
        self.assertIn("Control button C has not been clicked", dead)


class XInspectorOffX11Test(unittest.TestCase):
    def test_missing_libraries_are_harmless(self):
        def loader(name):
            raise OSError(f"lib{name} not found")
        xi = XInspector(loader=loader)
        self.assertFalse(xi.available)
        self.assertIn("not found", xi.reason)


if __name__ == "__main__":
    unittest.main()
