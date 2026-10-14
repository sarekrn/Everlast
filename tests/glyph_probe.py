import sys, ctypes, ctypes.util, time
sys.path.insert(0, ""+__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))+"")
from eqtool_native.ui import clickthrough as C
x = ctypes.CDLL(ctypes.util.find_library("X11"))
x.XOpenDisplay.restype = ctypes.c_void_p; x.XOpenDisplay.argtypes=[ctypes.c_char_p]
x.XDefaultRootWindow.restype = ctypes.c_ulong; x.XDefaultRootWindow.argtypes=[ctypes.c_void_p]
x.XCreateSimpleWindow.restype = ctypes.c_ulong
x.XCreateSimpleWindow.argtypes=[ctypes.c_void_p,ctypes.c_ulong]+[ctypes.c_int]*2+[ctypes.c_uint]*3+[ctypes.c_ulong]*2
x.XMapWindow.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
x.XCreateGC.restype=ctypes.c_void_p; x.XCreateGC.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p]
x.XSetForeground.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
x.XFillRectangle.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_void_p]+[ctypes.c_int]*2+[ctypes.c_uint]*2
x.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int]
d = x.XOpenDisplay(None); root = x.XDefaultRootWindow(d)
w = x.XCreateSimpleWindow(d, root, 0,0,200,60,0,0,0x0d1117)
x.XMapWindow(d,w); x.XSync(d,0); time.sleep(.2)
gc = x.XCreateGC(d,w,0,None); x.XSetForeground(d,gc,0xFFD400)
x.XFillRectangle(d,w,gc,10,5,30,20); x.XFillRectangle(d,w,gc,100,30,50,10); x.XSync(d,0)
ct = C.ClickThrough.__new__(C.ClickThrough)
ct.available=True; ct._x=x; ct._dpy=d
C.ClickThrough.shape_available = property(lambda s: True)
ct.inner_id = lambda win: w
print(ct.glyph_rects(None, 200, 60))
