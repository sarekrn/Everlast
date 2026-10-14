"""An X error on one of our connections must not kill the program.

Each case runs in its own process, because the failure being guarded against is the
whole process exiting. Skipped without an X display."""
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def run(code, prelude=""):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-c", prelude + textwrap.dedent(code)], env=env,
                          capture_output=True, text=True, timeout=60)


PRELUDE = """
import ctypes, sys
from eqtool_native.ui.clickthrough import ClickThrough, Decorations, _toplevel_chain
from eqtool_native.ui.xinspect import XInspector
from eqtool_native.ui import xguard

class Root:
    class tk:
        @staticmethod
        def call(*a): return "x11"

class Win:                       # a Tk window whose X window does not exist (or no longer does)
    def __init__(self, wid): self.wid = wid
    def update_idletasks(self): pass
    def winfo_pointerxy(self): return (0, 0)
    def winfo_exists(self): return True
    def wm_frame(self): return hex(self.wid)
    def winfo_id(self): return self.wid
BOGUS = 0x3e00113                # the id from the crash report
"""


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display")
class XGuardTests(unittest.TestCase):
    def test_without_the_guard_one_bad_request_kills_the_process(self):
        """The crash as reported: X_QueryTree on a window that does not exist."""
        r = run("""
            import ctypes, ctypes.util
            x = ctypes.CDLL(ctypes.util.find_library("X11"))
            x.XOpenDisplay.restype = ctypes.c_void_p
            x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 4
            d = x.XOpenDisplay(None)
            a, b, c, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
            x.XQueryTree(d, 0x3e00113, ctypes.byref(a), ctypes.byref(b), ctypes.byref(c), ctypes.byref(n))
            print("SURVIVED")
        """)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("SURVIVED", r.stdout)
        self.assertIn("BadWindow", r.stderr)

    def test_with_the_guard_everything_we_do_survives_missing_windows(self):
        r = run("""
            ct, dec, xi = ClickThrough(Root()), Decorations(Root()), XInspector()
            w = Win(BOGUS)
            assert _toplevel_chain(ct._x, ct._dpy, BOGUS) == []          # the walk gives up quietly
            assert ct.chain(w) == []
            assert ct.window_id(w) == 0
            assert ct.set(w, True) is False                               # no windows: nothing to do
            assert ct.set_input(w, [(0, 0, 5, 5)]) is False
            assert ct.set_shape(w, [(0, 0, 5, 5)]) == 0
            assert dec.set_opacity(w, 0.5) is False and dec.set(w, False) is False
            assert xi.attrs(BOGUS) is None or True                        # direct calls on a missing window
            xi.tree(BOGUS); xi.rects(BOGUS, 0); xi.opacity(BOGUS); xi.name(BOGUS)
            ct._x.XSync(ct._dpy, 0)
            print("SURVIVED errors_ignored=%d" % xguard.count)
        """, PRELUDE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SURVIVED", r.stdout)

    def test_a_window_that_disappears_between_two_requests(self):
        """Tk replaces the wrapper around a toplevel: the stack we found is gone a moment later."""
        r = run("""
            ct = ClickThrough(Root()); x, dpy = ct._x, ct._dpy
            x.XCreateSimpleWindow.restype = ctypes.c_ulong
            x.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_int] * 2 + [ctypes.c_uint] * 3 + [ctypes.c_ulong] * 2
            x.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            x.XDestroyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
            root = x.XDefaultRootWindow(dpy)
            wrapper = x.XCreateSimpleWindow(dpy, root, 10, 10, 50, 50, 0, 0, 0)
            inner = x.XCreateSimpleWindow(dpy, wrapper, 0, 0, 50, 50, 0, 0, 0)
            x.XMapWindow(dpy, wrapper); x.XSync(dpy, 0)
            w = Win(inner)
            assert [i for i, _, _ in ct.chain(w)] == [inner, wrapper]     # found while it exists
            x.XDestroyWindow(dpy, wrapper); x.XSync(dpy, 0)                # ...then Tk throws it away
            assert ct.chain(w) == []
            assert ct.set(w, True) is False
            assert ct.set_shape(w, [(0, 0, 4, 4)]) == 0
            print("SURVIVED")
        """, PRELUDE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SURVIVED", r.stdout)

    def test_errors_in_unregistered_connections_still_reach_the_previous_handler(self):
        """Tk's own connection must keep its behaviour: we only swallow errors on ours."""
        r = run("""
            seen = []
            x = ctypes.CDLL(ctypes.util.find_library("X11")) if False else None
            import ctypes.util
            xl = ctypes.CDLL(ctypes.util.find_library("X11"))
            H = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(xguard._XErrorEvent))
            def previous(dpy, ev):                                     # stands in for Tk's handler
                seen.append(ev.contents.resourceid); return 0
            keep = H(previous)
            xl.XSetErrorHandler.restype = H; xl.XSetErrorHandler.argtypes = [H]
            xl.XSetErrorHandler(keep)                                  # installed BEFORE ours, like Tk's
            ct = ClickThrough(Root())                                  # installs the guard, chaining to it
            xl.XOpenDisplay.restype = ctypes.c_void_p
            other = xl.XOpenDisplay(None)                              # a connection that is not ours
            xl.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 4
            a, b, c, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
            xl.XQueryTree(other, 0x12345, ctypes.byref(a), ctypes.byref(b), ctypes.byref(c), ctypes.byref(n))
            _toplevel_chain(ct._x, ct._dpy, 0x54321)                   # an error on OUR connection
            print("previous handler saw:", [hex(i) for i in seen], "| ours ignored:", xguard.count)
        """, PRELUDE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("previous handler saw: ['0x12345'] | ours ignored: 1", r.stdout)


class SwitchOffTests(unittest.TestCase):
    def test_the_environment_switch_turns_every_x_trick_off(self):
        r = run("""
            import os
            os.environ["EQTOOL_NO_XTRICKS"] = "1"
            from eqtool_native.ui.clickthrough import ClickThrough, Decorations
            from eqtool_native.ui.xinspect import XInspector
            class Root:
                class tk:
                    @staticmethod
                    def call(*a): return "x11"
            ct, dec, xi = ClickThrough(Root()), Decorations(Root()), XInspector()
            assert not ct.available and not dec.available and not xi.available
            assert not ct.shape_available and ct.set(object(), True) is False
            print("OFF:", ct.reason)
        """)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("turned off by EQTOOL_NO_XTRICKS", r.stdout)

    def test_zero_or_empty_leaves_them_on(self):
        for value in ("", "0"):
            r = run(f"""
                import os
                os.environ["EQTOOL_NO_XTRICKS"] = {value!r}
                from eqtool_native.ui.clickthrough import tricks_disabled
                print(tricks_disabled())
            """)
            self.assertEqual(r.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
