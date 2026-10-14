"""Keep a failed X request from killing the program.

Xlib's default error handler prints the error and then calls exit(): one failed
request, say a question about a window Tk has just replaced, ends the whole
program. Tk replaces windows often (it rebuilds the wrapper around a toplevel when
you change decorations, and our own connections are separate from Tk's), so a
request that fails now and then is to be expected, not fatal.

`watch()` installs one handler for the process. Errors on the connections we
registered are logged and ignored, and the failed call just reports failure; errors
on anything else (Tk's own connection) go to the handler that was there before,
so Tk is unaffected. ctypes only, no tkinter.
"""
import ctypes
import logging

log = logging.getLogger(__name__)


class _XErrorEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p),
                ("resourceid", ctypes.c_ulong), ("serial", ctypes.c_ulong),
                ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))
_ours = set()          # Display pointers of connections that belong to us
_prev = None           # the handler that was installed before ours (Tk's)
_callback = None       # kept alive for as long as the process runs
recent = []            # [(error_code, request_code, resource id)], newest last
count = 0


def _on_error(display, event):
    global count
    try:
        ev = event.contents
        if int(ev.display or display or 0) in _ours:
            count += 1
            recent.append((int(ev.error_code), int(ev.request_code), int(ev.resourceid)))
            del recent[:-20]
            log.warning("X error ignored: code %d, request %d, resource 0x%x",
                        ev.error_code, ev.request_code, ev.resourceid)
            return 0
        if _prev is not None:
            return _prev(display, event)
    except Exception:       # never let this callback raise into Xlib
        pass
    return 0


def watch(x, dpy):
    """Register a connection as ours (and install the guard the first time)."""
    global _prev, _callback
    try:
        _ours.add(int(dpy))
        if _callback is None:
            x.XSetErrorHandler.restype = _HANDLER
            x.XSetErrorHandler.argtypes = [_HANDLER]
            _callback = _HANDLER(_on_error)
            _prev = x.XSetErrorHandler(_callback)
        return True
    except Exception:
        log.exception("could not install the X error guard")
        return False
