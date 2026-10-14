"""Main application: control window, settings dialog and the polling loop."""
import logging
import os
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import APP_NAME, AUTHOR, APP_TITLE, VERSION_LABEL
from ..engine import Engine
from ..logwatch import LogWatcher
from ..mobinfo import MobInfoService
from ..settings import autodetect_eq_dir, cache_dir
from ..engine import Alert, estimate_clock_offset
from ..announcer import Announcer
from ..speech import DEFAULT_CLIPS, ClipPlayer, Speaker, install_hint
from ..spells import SpellDB
from .base import P, fmt_secs, make_fonts, setup_style
from .clickthrough import ClickThrough, Decorations
from .diagnostics import ClickTestDialog, DiagnosticsDialog, on_wayland, session_label
from .xinspect import XInspector
from .tray import TrayIcon
from .windows import (AlertWindow, DpsWindow, MapWindow, MobTimersWindow, MobWindow,
                      RespawnTimersWindow, SieveWindow,
                      TimersWindow)

log = logging.getLogger(__name__)

POLL_MS = 200
ICON_DIR = Path(__file__).resolve().parent.parent / "icons"


class App:
    OPACITY_ROWS = [("timers", "Buff timers"), ("mobtimers", "Mob timers"),
                    ("respawns", "Respawn timers"), ("dps", "DPS"),
                    ("sieve", "Mana sieve"), ("map", "Map"), ("mob", "Mob info"), ("alerts", "Alert banner")]
    def __init__(self, settings):
        self.settings = settings
        self.root = tk.Tk()
        self.root.title(f"{APP_TITLE} {VERSION_LABEL}")
        self.root.configure(bg=P["bg"])
        self.root.report_callback_exception = self._tk_error
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        setup_style(self.root)
        self.fonts = make_fonts(self.root, int(self.settings.num("font_size", 10)))
        self.clickthrough = ClickThrough(self.root)
        self.decorations = Decorations(self.root)
        self._xi = XInspector()
        self._compositor = None   # None = not checked yet
        self._comp_checked = 0.0
        self._last_lift = 0.0
        self._last_remap = time.monotonic()

        self.spells = SpellDB()
        self.engine = Engine(settings, self.spells)
        self.watcher = LogWatcher(settings["eq_dir"], settings["log_file"])
        self.mobinfo = MobInfoService(cache_dir() / "mobinfo.json")
        self.engine.respawn_lookup = self._cached_respawn
        self._spells_msg = ""
        self.clock_offset = 0.0   # seconds to add to log timestamps (see engine)

        self._build_main()
        self.windows = {
            "timers": TimersWindow(self),
            "mobtimers": MobTimersWindow(self),
            "respawns": RespawnTimersWindow(self),
            "dps": DpsWindow(self),
            "sieve": SieveWindow(self),
            "map": MapWindow(self),
            "mob": MobWindow(self),
        }
        self.speaker = Speaker(settings["tts_command"], settings["tts_engine"])
        self.clips = ClipPlayer()
        self.announcer = Announcer(self.speaker, self.clips)
        self.alert_win = AlertWindow(self)
        self.on_window_toggled()
        self._last_save = time.monotonic()
        self.tray = None
        self._set_window_icon()
        self.root.bind("<Unmap>", self._on_unmap)
        self.update_tray()

        if not settings["eq_dir"]:
            self.status_var.set("Looking for your EverQuest folder...")
            threading.Thread(target=self._autodetect, daemon=True).start()
        else:
            self.load_spells()

    # ------------------------------------------------------------ building
    def _build_main(self):
        conf = (self.settings["windows"] or {}).get("main", {})
        try:
            self.root.geometry(conf.get("geometry") or "340x560+40+40")
        except tk.TclError:
            pass
        fr = ttk.Frame(self.root, padding=10)
        fr.pack(fill="both", expand=True)
        # Pinned to the bottom (packed first), so a short window clips the status
        # text rather than these buttons.
        bottom = ttk.Frame(fr)
        bottom.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Settings", command=self.open_settings).grid(
            row=0, column=0, sticky="ew", padx=2)
        tools = ttk.Menubutton(bottom, text="Tools")
        menu = tk.Menu(tools, tearoff=0)
        menu.add_command(label="Refresh overlays", command=self.refresh_overlays)
        self.log_menu = tk.Menu(menu, tearoff=0, postcommand=self._fill_log_menu)
        self.log_choice = tk.StringVar(value="")
        menu.add_cascade(label="Character log", menu=self.log_menu)
        menu.add_command(label="Diagnostics...", command=lambda: DiagnosticsDialog(self))
        menu.add_command(label="Click-through test...", command=lambda: ClickTestDialog(self))
        tools["menu"] = menu
        tools.grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(bottom, text="Exit", style="Exit.TButton", command=self.quit).grid(
            row=0, column=2, sticky="ew", padx=2)
        for c in range(3):
            bottom.columnconfigure(c, weight=1, uniform="bottom")

        head = ttk.Frame(fr)
        head.pack(fill="x")
        big = self.fonts["bold"].copy()
        big.configure(size=int(self.fonts["bold"].cget("size")) + 6)
        ttk.Label(head, text=APP_NAME, style="Head.TLabel", font=big).pack(side="left")
        ttk.Label(head, text=f"  by {AUTHOR}  \u00b7  {VERSION_LABEL}", style="Dim.TLabel",
                  font=self.fonts["small"]).pack(side="left", pady=(6, 0))
        self.info_var = tk.StringVar(value="")
        ttk.Label(fr, textvariable=self.info_var, font=self.fonts["small"],
                  justify="left", wraplength=290).pack(anchor="w", pady=(2, 6))

        nb = ttk.Notebook(fr)
        nb.pack(fill="both", expand=True)
        wtab = ttk.Frame(nb, padding=(8, 8))
        otab = ttk.Frame(nb, padding=(8, 8))
        nb.add(wtab, text="Windows")
        nb.add(otab, text="Opacity")

        # ---- Windows tab: what is open, and the lock
        grid = wtab
        self.toggle_buttons = {}
        for i, (key, text) in enumerate(
                [("timers", "Buff timers"), ("mobtimers", "Mob timers"),
                 ("respawns", "Respawn timers"), ("dps", "DPS"),
                 ("sieve", "Mana sieve"), ("map", "Map"), ("mob", "Mob info")]):
            b = ttk.Button(grid, text=text, command=lambda k=key: self.windows[k].toggle())
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
            self.toggle_buttons[key] = (b, text)
        ttk.Separator(grid).grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)
        self.alert_btn = ttk.Button(grid, text="Position alerts",
                                    command=lambda: self.alert_win.toggle_positioning())
        self.alert_btn.grid(row=5, column=0, sticky="ew", padx=2, pady=2)
        self.lock_btn = ttk.Button(grid, text="Lock overlays", command=self.toggle_lock)
        self.lock_btn.grid(row=5, column=1, sticky="ew", padx=2, pady=2)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        # ---- Opacity tab: one slider per window, and which ones let clicks through
        og = otab
        self.opacity_vars, self.opacity_pcts, self.passthrough_vars = {}, {}, {}
        per = self.settings["window_opacity"] or {}
        ttk.Label(og, text="Click-through", style="Dim.TLabel", font=self.fonts["small"]).grid(
            row=0, column=3, sticky="e", padx=(6, 0))
        for i, (key, text) in enumerate(self.OPACITY_ROWS, start=1):
            if key == "alerts":
                start_val = self.settings.num("alert_opacity", 0.85)
            else:
                start_val = per.get(key, self.settings.num("opacity", 0.92))
            ttk.Label(og, text=text, font=self.fonts["small"]).grid(row=i, column=0, sticky="w")
            var = tk.DoubleVar(value=float(start_val))
            scale = ttk.Scale(og, from_=0.0, to=1.0, variable=var,
                              command=lambda v, k=key: self.set_opacity(k, float(v), save=False))
            scale.grid(row=i, column=1, sticky="ew", padx=6, pady=2)
            scale.bind("<ButtonRelease-1>", lambda e: self.settings.save())
            pct = ttk.Label(og, width=5, anchor="e", font=self.fonts["small"])
            pct.grid(row=i, column=2, sticky="e")
            pct.configure(text=f"{int(round(float(start_val) * 100))}%")
            self.opacity_vars[key], self.opacity_pcts[key] = var, pct
            if key == "alerts":
                ttk.Label(og, text="always", style="Dim.TLabel",
                          font=self.fonts["small"]).grid(row=i, column=3)
            else:
                choice = (self.settings["clickthrough_windows"] or {}).get(key)
                pv = tk.BooleanVar(value=(key != "map") if choice is None else bool(choice))
                ttk.Checkbutton(og, variable=pv,
                                command=lambda k=key: self.set_passthrough(k)).grid(row=i, column=3)
                self.passthrough_vars[key] = pv
        og.columnconfigure(1, weight=1)
        ttk.Label(og, text="The slider fades only the background; bars and text stay solid. "
                           "Click-through applies while locked. Ctrl + scroll over a window "
                           "also sets its opacity.", style="Dim.TLabel",
                  font=self.fonts["small"], wraplength=270, justify="left").grid(
            row=len(self.OPACITY_ROWS) + 1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(fr, textvariable=self.status_var, style="Dim.TLabel", font=self.fonts["small"],
                  wraplength=290, justify="left").pack(anchor="w", pady=(6, 0))
        self._fit_main_window()

    # ------------------------------------------------------------ tray
    def _set_window_icon(self):
        """Everlast's icon on the title bar / taskbar."""
        try:
            self._icons = [tk.PhotoImage(file=str(ICON_DIR / f"everlast-{n}.png"))
                           for n in (64, 48, 32, 16)]
            self.root.iconphoto(True, *self._icons)
        except (tk.TclError, OSError):
            log.info("window icon not loaded", exc_info=True)

    def update_tray(self):
        """Create or remove the tray icon to match the settings."""
        want = bool(self.settings["tray_icon"])
        if want and self.tray is None:
            self.tray = TrayIcon(ICON_DIR, on_click=self.toggle_main,
                                 on_menu=self.tray_menu, title=APP_TITLE)
            if not self.tray.available:
                log.info("tray unavailable: %s", self.tray.reason)
            else:
                self._poll_tray()
                if self.settings["start_minimized"]:
                    self.root.after(1500, self._start_hidden)
        elif not want and self.tray is not None:
            self.tray.close()
            self.tray = None
            self.show_main()

    def tray_ok(self):
        return self.tray is not None and self.tray.available and self.tray.docked

    def _poll_tray(self):
        if self.tray is None:
            return
        self.tray.poll()
        self.root.after(150, self._poll_tray)

    def _start_hidden(self):
        if self.tray_ok():
            self.root.withdraw()

    def _on_unmap(self, event):
        if event.widget is not self.root or not self.settings["minimize_to_tray"]:
            return
        self.root.after(1200, self._tray_hide_if_iconic)   # after the WM's minimize animation

    def _tray_hide_if_iconic(self):
        try:
            if self.tray_ok() and self.root.state() == "iconic":
                self.root.withdraw()
        except tk.TclError:
            pass

    def show_main(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def toggle_main(self):
        try:
            if self.root.state() in ("withdrawn", "iconic"):
                self.show_main()
            else:
                self.root.withdraw()
        except tk.TclError:
            pass

    def tray_menu(self, x, y):
        m = tk.Menu(self.root, tearoff=0)
        hidden = self.root.state() in ("withdrawn", "iconic")
        m.add_command(label=("Show Everlast" if hidden else "Hide to tray"),
                      command=self.toggle_main)
        m.add_command(label=("Unlock overlays" if self.settings["overlays_locked"]
                             else "Lock overlays"), command=self.toggle_lock)
        m.add_command(label="Settings...", command=lambda: (self.show_main(), self.open_settings()))
        m.add_separator()
        m.add_command(label="Exit", command=self.quit)
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _fill_log_menu(self):
        """Tools > Character log: follow the newest log automatically, or pick one."""
        m = self.log_menu
        m.delete(0, "end")
        forced = self.watcher.forced
        self.log_choice.set(forced)
        m.add_radiobutton(label="Automatic (the log being written)", value="",
                          variable=self.log_choice, command=lambda: self.pick_log(""))
        m.add_separator()
        for path, char, server, mt in self.watcher.list_logs()[:15]:
            ago = max(0, int(time.time() - mt))
            when = (f"{ago // 60}m ago" if ago < 3600 else f"{ago // 3600}h ago" if ago < 172800
                    else f"{ago // 86400}d ago")
            m.add_radiobutton(label=f"{char} ({server}), last written {when}", value=path,
                              variable=self.log_choice, command=lambda p=path: self.pick_log(p))

    def pick_log(self, path):
        self.watcher.force(path)
        self.status_var.set("Following the log you picked." if path
                            else "Following whichever log is being written.")

    def _fit_main_window(self):
        """The window grows as controls are added, but the size saved by an older
        version stays small and would hide the newest controls: enforce the
        content's size as a minimum, with room for a few lines of status text."""
        try:
            self.root.update_idletasks()
            need_w = int(self.root.winfo_reqwidth())
            need_h = int(self.root.winfo_reqheight()) + 70
        except (tk.TclError, TypeError, ValueError):
            return
        self.root.minsize(need_w, need_h)
        try:
            size, _, pos = self.root.geometry().partition("+")
            w, h = (int(v) for v in size.split("x"))
            if w < need_w or h < need_h:
                self.root.geometry(f"{max(w, need_w)}x{max(h, need_h)}" + (f"+{pos}" if pos else ""))
        except (tk.TclError, ValueError):
            pass

    def on_window_toggled(self):
        for key, (b, text) in getattr(self, "toggle_buttons", {}).items():
            w = getattr(self, "windows", {}).get(key)
            if w:
                b.configure(text=("\u2713 " if w.is_visible() else "") + text)
        if hasattr(self, "lock_btn"):
            self.lock_btn.configure(text="Unlock overlays" if self.settings["overlays_locked"]
                                    else "Lock overlays")
        aw = getattr(self, "alert_win", None)
        if aw is not None:
            self.alert_btn.configure(text="Lock alerts" if aw.positioning else "Position alerts")

    # ------------------------------------------------------------ data
    def _autodetect(self):
        found = None
        try:
            found = autodetect_eq_dir()
        except Exception:
            log.exception("autodetect failed")
        self.root.after(0, lambda: self._autodetect_done(found))

    def _autodetect_done(self, found):
        if found:
            self.settings["eq_dir"] = found
            self.settings.save()
            self.watcher.configure(found, self.settings["log_file"])
            self.load_spells()
            self.windows["map"].refresh_zone_list()
        else:
            self.status_var.set("EverQuest folder not found. Open Settings and choose it "
                                "(the folder with eqgame.exe inside your Wine prefix).")
            self.open_settings()

    def load_spells(self):
        path = SpellDB.find(self.settings["eq_dir"])
        if not path:
            self._spells_msg = "spells_us.txt not found: spell timers are off."
            return
        try:
            self.spells.load(path)
            self._spells_msg = f"{len(self.spells):,} spells loaded."
        except Exception as e:
            log.exception("spell load failed")
            self._spells_msg = f"Could not read spells_us.txt ({e})."

    def _cached_respawn(self, mob):
        info = self.mobinfo.cached(mob)
        return info.respawn_seconds if info else 0

    # ------------------------------------------------------------ loop
    def _tick(self):
        try:
            lines = self.watcher.poll()
            if self.watcher.character and self.watcher.character != self.engine.character:
                log.info("character is now %s", self.watcher.character)
                self.engine.switch_character(self.watcher.character)
            history = self.watcher.take_history()
            if history:
                self.engine.prime(history)
            if lines:
                off = estimate_clock_offset(lines, time.time())
                if off is not None and off != self.clock_offset:
                    log.info("log clock offset %+.0f s (was %+.0f s)", off, self.clock_offset)
                    self.clock_offset = off
            for line in lines:
                self.engine.feed_raw(line, self.clock_offset)
            now = time.time()
            self.engine.tick(now)
            self._drain_events()
            for info in self.mobinfo.poll():
                self.windows["mob"].on_result(info)
            for w in self.windows.values():
                if w.is_visible():
                    try:
                        w.refresh(now)
                    except Exception:
                        log.exception("refresh of %s failed", w.name)
            self.alert_win.refresh(now)
            for w in self.windows.values():
                w.keep_on_top()
            if now - self._last_lift > 1:
                # Once a second: re-apply click-through and shapes (Tk can recreate
                # the X window under them) and keep unmanaged overlays on top.
                self._last_lift = now
                for w in self.windows.values():
                    try:
                        w.maintain()
                    except Exception:
                        log.exception("maintaining %s failed", w.name)
                try:
                    self.alert_win.maintain()
                except Exception:
                    log.exception("maintaining the alert banner failed")
                every = self.settings.num("overlay_refresh_seconds", 0)
                if every > 0 and time.monotonic() - self._last_remap >= every:
                    self.refresh_overlays()
            self._update_info()
            if time.monotonic() - self._last_save > 60:
                self._last_save = time.monotonic()
                self._remember_all()
                self.settings.save()
        except Exception:
            log.exception("main loop iteration failed")
        finally:
            self.root.after(POLL_MS, self._tick)

    def _drain_events(self):
        ev = self.engine.events
        while ev:
            kind, data = ev.popleft()
            try:
                if kind == "zone":
                    self.windows["map"].on_zone(data)
                elif kind == "loc":
                    self.windows["map"].on_loc()
                elif kind == "con":
                    self.windows["mob"].on_con(data)
                elif kind == "alert":
                    self.handle_alert(data)
            except Exception:
                log.exception("event %s failed", kind)

    SHOW_SETTING = {"charm": "alert_charm", "root": "alert_root", "worn": "alert_worn_off",
                    "timer_you": "alert_timers", "debuff": "alert_timers",
                    "self_fade": "alert_self_fades", "fizzle": "alert_fizzle",
                    "interrupt": "alert_interrupt", "invite": "alert_invite", "trap": "alert_trap", "con": "alert_con", "boat": "alert_boat",
                    "ringwar": "alert_ringwar", "ch_warn": "alert_ch_warn", "roar": "alert_roar",
                    "enrage": "alert_enrage", "dt": "alert_dt", "trigger": "alert_triggers", "watch": "alert_watch",
                    "custom": "alert_timers", "spawn": "alert_spawns",
                    "timer_other": "alert_timers_others", "recast": "alert_recast",
                    "my_buff": "alert_my_buffs"}

    def handle_alert(self, alert, force=False):
        s = self.settings
        show = force or (s["alerts_enabled"] and s[self.SHOW_SETTING.get(alert.kind, "alert_timers")])
        show = show and (alert.show or force)
        if show:
            self.alert_win.push(alert.text, time.time(), alert.key)
        steps = self.speech_steps(alert, show, force)
        if steps:
            self.announcer.announce(steps, urgent=alert.kind in self.URGENT)
        elif self.wants_sound(alert):
            self.root.bell()          # no clip and no voice at all: at least make a sound

    # What must be heard at once, ahead of the rest.
    URGENT = ("charm", "root", "fizzle", "interrupt", "invite", "trap", "roar", "enrage", "dt", "watch",
              "ch_warn")

    def wants_sound(self, alert):
        s = self.settings
        want = {"charm": "tts_charm_root", "root": "tts_charm_root",
                "fizzle": "tts_fizzle", "interrupt": "tts_interrupt", "invite": "tts_invite", "trap": "tts_trap", "con": "tts_con", "boat": "tts_boat",
                "ringwar": "tts_ringwar", "ch_warn": "tts_ch_warn", "roar": "tts_roar",
                "enrage": "tts_enrage", "dt": "tts_dt"}.get(alert.kind)
        return bool(want and s["tts_enabled"] and s[want])

    def speech_steps(self, alert, show, force=False):
        """What to play for an alert: a list of ("clip", path) / ("say", text)."""
        s = self.settings
        if not (s["tts_enabled"] or force):
            return []
        if not (alert.speak or force):
            return []
        kind, text = alert.kind, alert.say or alert.text
        voice = [("say", text)] if self.speaker.available else []
        if kind in ("charm", "root"):
            want = s["tts_charm_root"]
            clip = self.clip_for(kind)
            steps = [("clip", clip)] if clip else voice
        elif kind in ("self_fade", "my_buff"):
            # A buff fading: say which one (and on whom), the recorded "Buff faded", or both.
            want = s["tts_self_fades"] if kind == "self_fade" else s["tts_my_buffs"]
            clip = self.clip_for("my_buff")
            recorded = [("clip", clip)] if clip else []
            mode = s["fade_voice"]
            if mode == "clip":
                steps = recorded or voice
            elif mode == "both":
                steps = recorded + voice
            else:
                steps = voice or recorded
        elif kind == "trigger":
            want, steps = s["tts_triggers"], voice
        elif kind in ("fizzle", "interrupt", "invite", "trap", "con", "boat", "ringwar", "ch_warn", "roar", "enrage", "dt", "watch"):
            # Said in the computer voice: it names the spell (or what wore off).
            want = s["tts_" + kind]
            steps = voice
        else:
            want = show and s["tts_timers"] and kind != "worn"
            steps = voice
        return steps if (force or want) else []

    def test_voice_line(self, kind):
        """Run the whole announcement path with the saved settings."""
        lines = {"self_fade": ("Spirit of Wolf faded", "Spirit of Wolf faded"),
                 "my_buff": ("Spirit of Wolf faded on Bob", "Spirit of Wolf faded on Bob")}
        text, say = lines[kind]
        self.handle_alert(Alert(kind, text, say), force=True)

    def clip_path(self, kind):
        return (self.settings["voice_clips"] or {}).get(kind) or DEFAULT_CLIPS.get(kind)

    def clip_for(self, kind):
        """The recorded clip to play for this alert, or None to use the voice."""
        if not self.settings["voice_clips_enabled"] or kind not in DEFAULT_CLIPS:
            return None
        path = self.clip_path(kind)
        return path if self.clips.can_play(path) else None

    def set_opacity(self, key, value, save=True):
        """One window's opacity (main-window sliders). Other windows are untouched."""
        value = round(max(0.0, min(1.0, value)), 2)
        if key == "alerts":
            self.settings["alert_opacity"] = value
            if hasattr(self, "alert_win"):
                self.alert_win.apply_prefs()
        else:
            w = getattr(self, "windows", {}).get(key)
            if w:
                w.set_opacity(value)          # also stores it for that window only
            else:
                per = dict(self.settings["window_opacity"] or {})
                per[key] = value
                self.settings["window_opacity"] = per
        self.opacity_changed(key, value)
        if save:
            self.settings.save()

    def set_passthrough(self, key):
        """Per-window: let clicks through while locked, or stay interactive."""
        per = dict(self.settings["clickthrough_windows"] or {})
        per[key] = bool(self.passthrough_vars[key].get())
        self.settings["clickthrough_windows"] = per
        w = self.windows.get(key)
        if w and w.locked:
            w.apply_chrome()
        self.settings.save()

    def opacity_changed(self, key, value):
        """Keep a window's slider in step (e.g. after Ctrl+scroll on it)."""
        if key in getattr(self, "opacity_vars", {}):
            if abs(self.opacity_vars[key].get() - value) > 0.004:
                self.opacity_vars[key].set(value)
            self.opacity_pcts[key].configure(text=f"{int(round(value * 100))}%")

    def toggle_lock(self):
        locked = not self.settings["overlays_locked"]
        self.settings["overlays_locked"] = locked
        for w in self.windows.values():
            w.set_locked(locked)
        self.settings.save()
        self.on_window_toggled()
        if locked and not self.clickthrough.available:
            self.status_var.set("Overlays are locked (borderless), but click-through isn't "
                                f"available here: {self.clickthrough.reason}.")

    def test_alert(self):
        self.handle_alert(Alert("charm", "CHARM BREAK", "Charm break"), force=True)

    def _update_info(self):
        e = self.engine
        who = self.watcher.character or "no log yet"
        lines = [
            f"{who}  level {e.level}" + ("" if e.detected_level or
                                         self.settings.num("level_override", 0) else " (assumed)"),
            f"Zone: {e.zone or 'not detected yet (zone, or type /who)'}",
            f"Session: {session_label()}",
            f"Map: {self.windows['map'].map_status}",
        ]
        self.info_var.set("\n".join(lines))
        if self.watcher.path:
            msg = f"Reading {os.path.basename(self.watcher.path)}. {self._spells_msg}"
            quiet = self.watcher.quiet_seconds()
            if quiet > 90:
                msg += (f" Nothing new in this log for {int(quiet // 60)} min: if you are playing "
                        "this character, type /log on in game (a character whose logging is "
                        "off writes nothing, and no timers can work).")
        elif self.settings["eq_dir"]:
            msg = ("No eqlog_*.txt found yet. In game type /log on "
                   "(or set Log=TRUE in eqclient.ini). " + self._spells_msg)
        else:
            msg = self.status_var.get()
        if self._compositor is None or time.monotonic() - self._comp_checked > 30:
            self._comp_checked = time.monotonic()
            try:
                self._compositor = (True if (not self._xi.available or on_wayland())
                                    else self._xi.compositor_running())
            except Exception:
                self._compositor = True
        if self._compositor is False:
            msg += (" No compositing manager is running, so window opacity has no effect "
                    "(see Diagnostics).")
        if on_wayland() and self.settings["overlays_locked"]:
            msg += (" Wayland: click-through cannot work for these windows on this desktop; "
                    "locked timer windows shrink to their bars. An X11 session fixes it fully.")
        if abs(self.clock_offset) >= 60:
            h = abs(self.clock_offset) / 3600.0
            ahead = self.clock_offset < 0
            msg += (f" The game's clock is {h:g}h {'ahead of' if ahead else 'behind'} this PC "
                    "(a Wine time zone difference); timers are corrected for it.")
        if e.errors:
            msg += f"  ({e.errors} lines skipped, see log)"
        if self.settings["overlays_locked"] and not self.clickthrough.available:
            msg += (" Overlays are locked, but click-through isn't available here "
                    f"({self.clickthrough.reason}).")
        self.status_var.set(msg)

    def _tk_error(self, exc, val, tb):
        log.error("Tk callback error", exc_info=(exc, val, tb))

    # ------------------------------------------------------------ settings
    def open_settings(self):
        SettingsDialog(self)

    def apply_settings(self, eq_changed):
        self.watcher.configure(self.settings["eq_dir"], self.settings["log_file"])
        if eq_changed:
            self.load_spells()
            self.windows["map"].refresh_zone_list()
        size = int(self.settings.num("font_size", 10))
        for key, f in self.fonts.items():
            f.configure(size=max(7, size - 2) if key == "small" else size)
        for w in self.windows.values():
            w.apply_prefs()
            if w.solid or w.borderless != (w.locked or bool(self.settings["hide_borders"])):
                w.apply_chrome()
        self.alert_win.apply_prefs()
        self.speaker.configure(self.settings["tts_command"], self.settings["tts_engine"])
        self.update_tray()
        self.windows["map"].draw()
        self.settings.save()

    def _remember_all(self):
        for w in self.windows.values():
            w.remember()
        try:
            wins = dict(self.settings["windows"] or {})
            wins["main"] = {"geometry": self.root.geometry(), "visible": True}
            self.settings["windows"] = wins
        except tk.TclError:
            pass

    def quit(self):
        try:
            self.announcer.close()
            if self.alert_win.positioning:
                self.alert_win.remember()
            self._remember_all()
            self.settings.save()
        finally:
            if self.tray is not None:
                self.tray.close()
            self.root.destroy()

    def refresh_overlays(self):
        """Unmap and re-map every visible overlay (what hovering over one does)."""
        self._last_remap = time.monotonic()
        if any(getattr(w, "_ghosted", False) for w in self.windows.values()):
            return                      # the mouse is over one right now
        for w in self.windows.values():
            try:
                w.remap()
            except Exception:
                log.exception("refreshing %s failed", w.name)

    def ghost_enabled(self):
        """Hide-while-hovered: the Wayland workaround. On by default only on a Wayland
        desktop, where a click on an overlay window really is lost; elsewhere clicks pass
        straight through, so a bar that vanished under the mouse would only be in the
        way. The setting overrides this either way."""
        v = self.settings["ghost_under_mouse"]
        return on_wayland() if v is None else bool(v)

    def _ghost_loop(self):
        try:
            wins = list(self.windows.values()) + [self.alert_win]
            if self.settings["overlays_locked"] or any(getattr(w, "_ghosted", False) for w in wins):
                px, py = self.root.winfo_pointerxy()
                now = time.monotonic()
                for w in wins:
                    w.update_ghost(px, py, now)
        except Exception:
            log.exception("ghost loop failed")
        finally:
            self.root.after(50, self._ghost_loop)

    def run(self):
        self.root.after(50, self._ghost_loop)
        self.root.after(POLL_MS, self._tick)
        self.root.mainloop()


class SettingsDialog:
    GENERAL_NUM = [
        ("level_override", "Character level (0 = auto)", "int", (0, 65)),
        ("font_size", "Font size", "int", (7, 24)),
        ("fight_timeout", "Fight ends after idle (s)", "int", (5, 120)),
        ("min_recast", "Show recasts longer than (s)", "int", (0, 3600)),
        ("default_spawn_seconds", "Default respawn (s)", "int", (10, 200000)),
        ("overlay_refresh_seconds", "Refresh overlays every (s, 0 = never)", "int", (0, 3600)),
    ]
    GENERAL_BOOL = [
        ("always_on_top", "Keep windows on top"),
        ("tray_icon", "Show Everlast in the system tray"),
        ("minimize_to_tray", "    Minimizing leaves only the tray icon (no taskbar button)"),
        ("start_minimized", "    Start hidden in the tray"),
        ("hide_borders", "Hide title bars on overlay windows"),
        ("solid_timers", "Solid timer bars over a see-through background"),
        ("shrink_locked_overlays", "In play mode, shrink timer windows to fit a short list"),
        ("ghost_under_mouse", "Hide a locked overlay while the mouse is over it (so clicks reach the game)"),
        ("show_recast", "Show recast timers"),
        ("boat_timers", "Boat timers (from the boat captains' announcements)"),
        ("ringwar_timers", "Ring War round timers (Seneschal Aldikar's call to arms)"),
        ("ch_chain", "CH chain: follow \"001 CH -- Target\" calls and warn when I am next"),
        ("raid_aoe_timers", "Timers for raid mobs' zone-wide spells (breaths, Dragon Roar...)"),
        ("auto_spawn_timers", "Start a respawn timer for every kill"),
        ("spawn_needs_experience", "    ...only kills that give experience"),
        ("map_auto_show", "Open the map when you change zones"),
        ("map_negate_loc", "Map uses inverted /loc coordinates (standard)"),
    ]
    ALERT_BOOL = [
        ("alerts_enabled", "Show the alert banner"),
        ("alert_charm", "    Charm breaks"),
        ("alert_root", "    Root breaks"),
        ("alert_timers", "    Your buffs, debuffs, custom timers ending"),
        ("alert_spawns", "    A mob you killed is due to respawn"),
        ("alert_fizzle", "    Fizzled spells"),
        ("alert_interrupt", "    Interrupted spells"),
        ("alert_invite", "    Group / raid invitations"),
        ("alert_con", "    /consider: KOS or SAFE"),
        ("alert_boat", "    A boat is arriving"),
        ("alert_ringwar", "    Ring War: a round or a break starts"),
        ("alert_ch_warn", "    CH chain: the slot before mine was called"),
        ("alert_trap", "    Pansy trap tripped (Plane of Mischief)"),
        ("alert_roar", "    Dragon Roar: out, then a countdown to the next"),
        ("alert_enrage", "    A mob becomes enraged"),
        ("alert_dt", "    Death Touch (Fright and Dread name their target)"),
        ("alert_triggers", "    Custom triggers (Triggers tab)"),
        ("alert_watch", "    Watched spells wearing off (list below)"),
        ("alert_self_fades", "    Buffs on you fading"),
        ("alert_my_buffs", "    Buffs you cast on other players fading"),
        ("alert_timers_others", "    Other people's buffs on others ending"),
        ("alert_recast", "    Long recasts ready"),
        ("alert_worn_off", "    Any other spell wearing off"),
        ("tts_enabled", "Voice alerts"),
        ("tts_charm_root", "    Say \"Charm break\" / \"Root break\""),
        ("tts_fizzle", "    Say when a spell fizzles"),
        ("tts_interrupt", "    Say when a spell is interrupted"),
        ("tts_invite", "    Say when I am invited to a group"),
        ("tts_con", "    Say KOS or Safe when I /consider a mob"),
        ("tts_boat", "    Say when a boat is arriving"),
        ("tts_ringwar", "    Say each Ring War round and break"),
        ("tts_ch_warn", "    Say \"C H warning\" when my CH slot is next"),
        ("tts_trap", "    Say when a pansy trap is tripped"),
        ("tts_roar", "    Say Dragon Roar and its countdown"),
        ("tts_enrage", "    Say when a mob becomes enraged"),
        ("tts_dt", "    Say who is being death touched"),
        ("tts_triggers", "    Say the custom triggers that ask for it"),
        ("tts_watch", "    Say when a watched spell wears off"),
        ("tts_self_fades", "    Say when a buff on you fades"),
        ("tts_my_buffs", "    Say when a buff you cast on someone fades"),
        ("voice_clips_enabled", "    Use recorded clips (charm, root, buff faded)"),
        ("tts_timers", "    Also speak the other alerts shown"),
    ]
    ALERT_NUM = [
        ("alert_font_size", "Alert text size", "int", (16, 160)),
        ("alert_seconds", "Alert stays up (s)", "int", (1, 30)),
    ]

    def __init__(self, app):
        self.app = app
        s = app.settings
        self.top = tk.Toplevel(app.root)
        self.top.title(f"Settings - {APP_NAME}")
        self.top.configure(bg=P["bg"])
        self.top.transient(app.root)
        outer = ttk.Frame(self.top, padding=12)
        outer.pack(fill="both", expand=True)
        self.vars = {}

        row0 = ttk.Frame(outer)
        row0.pack(fill="x")
        ttk.Label(row0, text="EverQuest folder").pack(side="left", padx=(0, 8))
        self.vars["eq_dir"] = tk.StringVar(value=s["eq_dir"])
        ttk.Entry(row0, textvariable=self.vars["eq_dir"], width=50).pack(side="left", fill="x",
                                                                      expand=True)
        ttk.Button(row0, text="Browse", command=self.browse).pack(side="left", padx=(4, 0))
        ttk.Label(outer, text="The folder containing eqgame.exe, e.g. ~/.wine/drive_c/EverQuest "
                              "or a Lutris prefix.", style="Dim.TLabel",
                  font=app.fonts["small"]).pack(anchor="w", pady=(2, 10))

        self.str_vars = {}
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)

        def tab(title):
            f = ttk.Frame(nb, padding=(14, 10))
            nb.add(f, text=title)
            return f
        general, timers, mapt = tab("General"), tab("Timers"), tab("Map")
        alerts, voice = tab("Alerts"), tab("Voice")
        trig = tab("Triggers")

        def pick(specs, *keys):
            return [sp for sp in specs if sp[0] in keys]
        # --- General
        r = self._nums(general, pick(self.GENERAL_NUM, "level_override", "font_size",
                                     "overlay_refresh_seconds"), 0)
        r = self._bools(general, pick(self.GENERAL_BOOL, "always_on_top", "tray_icon",
                                      "minimize_to_tray", "start_minimized", "hide_borders",
                                      "solid_timers", "shrink_locked_overlays",
                                      "ghost_under_mouse"), r)
        # --- Timers
        r = self._nums(timers, pick(self.GENERAL_NUM, "fight_timeout", "min_recast",
                                    "default_spawn_seconds"), 0)
        r = self._bools(timers, pick(self.GENERAL_BOOL, "show_recast", "raid_aoe_timers", "boat_timers",
                                     "ringwar_timers", "ch_chain", "auto_spawn_timers",
                                     "spawn_needs_experience"), r)
        ttk.Label(timers, text="    CH chain: only calls starting with").grid(
            row=r, column=0, sticky="w", pady=3, padx=(0, 8))
        self.str_vars["ch_chain_tag"] = tk.StringVar(value=s["ch_chain_tag"] or "")
        ttk.Entry(timers, textvariable=self.str_vars["ch_chain_tag"], width=12).grid(
            row=r, column=1, sticky="w", pady=3)
        r += 1
        left = timers
        # --- which buffs the Buff Timers window lists
        ttk.Label(left, text="Buff timers show").grid(row=r, column=0, sticky="w", pady=(8, 3), padx=(0, 8))
        self.filter_labels = {"all": "every buff I can see",
                              "mine_and_me": "buffs I cast + all on me",
                              "mine": "only buffs I cast"}
        self.str_vars["buff_filter"] = tk.StringVar(
            value=self.filter_labels.get(s["buff_filter"], self.filter_labels["mine_and_me"]))
        ttk.Combobox(left, textvariable=self.str_vars["buff_filter"], state="readonly", width=24,
                     values=list(self.filter_labels.values())).grid(row=r, column=1, sticky="w", pady=(8, 3))
        r += 1
        # --- how tall each timer list is (the corner grip sets the same thing)
        heights = s["timer_view_heights"] or {}
        for key, text in (("timers", "Buff list height (px)"), ("mobtimers", "Mob list height (px)"),
                          ("respawns", "Respawn list height (px)")):
            w = app.windows[key]
            ttk.Label(left, text=text).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 8))
            self.str_vars["height:" + key] = tk.StringVar(value=str(int(heights.get(key) or w.default_view_height)))
            ttk.Spinbox(left, from_=60, to=1200, increment=10, width=8,
                        textvariable=self.str_vars["height:" + key]).grid(row=r, column=1, sticky="w")
            r += 1

        # --- Map
        self._bools(mapt, pick(self.GENERAL_BOOL, "map_auto_show", "map_negate_loc"), 0)
        # --- Alerts
        r = self._bools(alerts, [sp for sp in self.ALERT_BOOL if sp[0].startswith("alert")], 0)
        r = self._nums(alerts, self.ALERT_NUM, r)
        ttk.Label(alerts, text="Always announce when these\nwear off (comma separated)",
                  justify="left").grid(row=r, column=0, sticky="w", pady=(8, 3), padx=(0, 8))
        self.str_vars["watch_spells"] = tk.StringVar(value=", ".join(s["watch_spells"] or []))
        ttk.Entry(alerts, textvariable=self.str_vars["watch_spells"], width=30).grid(
            row=r, column=1, sticky="w", pady=(8, 3))
        r += 1
        ttk.Label(alerts, text="Extra trap phrases\n(log text, comma separated)",
                  justify="left").grid(row=r, column=0, sticky="w", pady=3, padx=(0, 8))
        self.str_vars["trap_phrases"] = tk.StringVar(value=", ".join(s["trap_phrases"] or []))
        ttk.Entry(alerts, textvariable=self.str_vars["trap_phrases"], width=30).grid(
            row=r, column=1, sticky="w", pady=3)
        # --- Voice
        right = voice
        r = self._bools(right, [sp for sp in self.ALERT_BOOL if not sp[0].startswith("alert")], 0)
        self.clip_paths = dict(s["voice_clips"] or {})
        self.clip_labels = {}
        for kind, text in (("charm", "Charm break clip"), ("root", "Root break clip"),
                           ("my_buff", "Buff faded clip")):
            ttk.Label(right, text=text).grid(row=r, column=0, sticky="w", pady=3)
            row = ttk.Frame(right)
            row.grid(row=r, column=1, sticky="w")
            lab = ttk.Label(row, style="Dim.TLabel", font=app.fonts["small"], width=16)
            lab.pack(side="left")
            self.clip_labels[kind] = lab
            ttk.Button(row, text="Play", style="Tool.TButton",
                       command=lambda k=kind: self.play_clip(k)).pack(side="left", padx=2)
            ttk.Button(row, text="Choose...", style="Tool.TButton",
                       command=lambda k=kind: self.choose_clip(k)).pack(side="left", padx=2)
            ttk.Button(row, text="Default", style="Tool.TButton",
                       command=lambda k=kind: self.default_clip(k)).pack(side="left", padx=2)
            r += 1
        self._show_clip_names()
        # --- computer voice
        installed = app.speaker.installed()
        ttk.Label(right, text="Computer voice").grid(row=r, column=0, sticky="w", pady=3)
        self.engine_labels = ["Auto (first one found)"] + installed
        cur = s["tts_engine"]
        self.str_vars["tts_engine"] = tk.StringVar(
            value=cur if cur in installed else self.engine_labels[0])
        ttk.Combobox(right, textvariable=self.str_vars["tts_engine"], values=self.engine_labels,
                     state="readonly", width=20).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(right, text="Buff fades are announced").grid(row=r, column=0, sticky="w", pady=3)
        self.fade_labels = {"voice": "by speaking the buff", "clip": "with the recorded clip",
                            "both": "clip, then the buff"}
        self.str_vars["fade_voice"] = tk.StringVar(value=self.fade_labels.get(s["fade_voice"],
                                                                           self.fade_labels["voice"]))
        ttk.Combobox(right, textvariable=self.str_vars["fade_voice"],
                     values=list(self.fade_labels.values()), state="readonly",
                     width=20).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(right, text="Custom voice command").grid(row=r, column=0, sticky="w", pady=3)
        self.vars["tts_command"] = tk.StringVar(value=s["tts_command"])
        ttk.Entry(right, textvariable=self.vars["tts_command"], width=22).grid(row=r, column=1,
                                                                             sticky="w")
        r += 1
        if installed:
            tip = (f"Found: {', '.join(installed)}. Use 'Test computer voice' to hear each one; if "
                   "one is silent, pick another. A custom command wins when set, with {text} "
                   "where the words go.")
        else:
            tip = ("No computer voice found. Install one with:  " + install_hint() +
                   "  then reopen Settings.")
        ttk.Label(right, text=tip, style="Dim.TLabel", font=app.fonts["small"],
                  wraplength=360, justify="left").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        brow = ttk.Frame(right)
        brow.grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(brow, text="Test computer voice", command=self.test_voice).pack(side="left")
        ttk.Button(brow, text="Test a buff fade", command=lambda: app.test_voice_line("my_buff")).pack(
            side="left", padx=4)
        ttk.Button(brow, text="Test banner + clip", command=app.test_alert).pack(side="left")
        r += 1
        self.test_label = ttk.Label(right, text="", style="Dim.TLabel", font=app.fonts["small"],
                                    wraplength=360, justify="left")
        self.test_label.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0))


        self._build_triggers(trig)

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.top.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=self.save).pack(side="right", padx=6)
        self.top.lift()
        self.top.focus_set()

    # ------------------------------------------------------------ triggers tab
    def _build_triggers(self, parent):
        from .. import triggers as T
        self.T = T
        cur = self.app.settings["triggers"]
        self.trig_list = [dict(t) for t in (T.default_triggers() if cur is None else cur)]
        ttk.Label(parent, text="When a log line matches, show text on the banner and/or say "
                               "something. Use {name} in the pattern to capture words and in "
                               "the text to use them. Same format as EQTool's triggers.",
                  style="Dim.TLabel", font=self.app.fonts["small"], wraplength=520,
                  justify="left").pack(anchor="w", pady=(0, 6))
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True)
        cols = ("on", "name", "search", "text", "say")
        self.trig_tree = ttk.Treeview(box, columns=cols, show="headings", height=9,
                                      selectmode="browse")
        for c, title, w in (("on", "On", 34), ("name", "Name", 140), ("search", "Looks for", 230),
                            ("text", "Shows", 130), ("say", "Says", 110)):
            self.trig_tree.heading(c, text=title)
            self.trig_tree.column(c, width=w, anchor="center" if c == "on" else "w",
                                  stretch=(c == "search"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.trig_tree.yview)
        self.trig_tree.configure(yscrollcommand=sb.set)
        self.trig_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.trig_tree.bind("<Double-1>", lambda e: self.edit_trigger())
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 0))
        for text, cmd in (("Add", self.add_trigger), ("Edit", self.edit_trigger),
                          ("On / off", self.toggle_trigger), ("Delete", self.delete_trigger),
                          ("Reset to defaults", self.reset_triggers),
                          ("Import...", self.import_triggers), ("Export...", self.export_triggers)):
            ttk.Button(row, text=text, style="Tool.TButton", command=cmd).pack(side="left",
                                                                               padx=(0, 4))
        test = ttk.Frame(parent)
        test.pack(fill="x", pady=(8, 0))
        ttk.Label(test, text="Try a log line").pack(side="left", padx=(0, 6))
        self.trig_try = tk.StringVar()
        e = ttk.Entry(test, textvariable=self.trig_try, width=44)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.try_trigger())
        ttk.Button(test, text="Test", style="Tool.TButton", command=self.try_trigger).pack(
            side="left", padx=4)
        self.trig_result = ttk.Label(parent, text="", style="Dim.TLabel",
                                     font=self.app.fonts["small"], wraplength=520, justify="left")
        self.trig_result.pack(anchor="w", pady=(4, 0))
        self._fill_triggers()

    def _fill_triggers(self, select=None):
        tree = self.trig_tree
        for item in tree.get_children():
            tree.delete(item)
        for i, t in enumerate(self.trig_list):
            tree.insert("", "end", iid=str(i), values=(
                "\u2713" if t.get("enabled") else "", t.get("name", ""), t.get("search", ""),
                t.get("text", "") if t.get("text_on") else "",
                t.get("audio", "") if t.get("audio_on") else ""))
        if select is not None and 0 <= select < len(self.trig_list):
            tree.selection_set(str(select))
            tree.see(str(select))

    def _selected_trigger(self):
        sel = self.trig_tree.selection()
        return int(sel[0]) if sel else None

    def add_trigger(self):
        new = {"id": self.T.next_id(self.trig_list), "enabled": True, "name": "New trigger",
               "search": "", "text_on": True, "text": "", "audio_on": False, "audio": ""}
        if self._trigger_dialog(new):
            self.trig_list.append(new)
            self._fill_triggers(len(self.trig_list) - 1)

    def edit_trigger(self):
        i = self._selected_trigger()
        if i is None:
            return
        t = dict(self.trig_list[i])
        if self._trigger_dialog(t):
            self.trig_list[i] = t
            self._fill_triggers(i)

    def toggle_trigger(self):
        i = self._selected_trigger()
        if i is not None:
            self.trig_list[i]["enabled"] = not self.trig_list[i].get("enabled")
            self._fill_triggers(i)

    def delete_trigger(self):
        i = self._selected_trigger()
        if i is not None:
            del self.trig_list[i]
            self._fill_triggers(min(i, len(self.trig_list) - 1))

    def reset_triggers(self):
        if messagebox.askyesno("Reset triggers", "Replace your triggers with EQTool's built-in "
                               "ones? Triggers you added will be lost.", parent=self.top):
            self.trig_list = self.T.default_triggers()
            self._fill_triggers()

    def import_triggers(self):
        path = filedialog.askopenfilename(parent=self.top, title="Import triggers",
                                          filetypes=[("Trigger files", "*.txt"), ("All files", "*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                got, bad = self.T.parse_file(f.read())
        except OSError as e:
            self.trig_result.configure(text=f"Could not read that file: {e}")
            return
        have = {t["id"]: i for i, t in enumerate(self.trig_list)}
        for t in got:                                    # same id: replace; otherwise add
            if t["id"] in have:
                self.trig_list[have[t["id"]]] = t
            else:
                self.trig_list.append(t)
        self._fill_triggers()
        self.trig_result.configure(text=f"Imported {len(got)} trigger(s)"
                                        + (f"; {bad} line(s) could not be read." if bad else "."))

    def export_triggers(self):
        path = filedialog.asksaveasfilename(parent=self.top, title="Export triggers",
                                            defaultextension=".txt",
                                            initialfile="everlast-triggers.txt")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.T.format_file(self.trig_list))
            self.trig_result.configure(text=f"Saved {len(self.trig_list)} trigger(s) to {path}")
        except OSError as e:
            self.trig_result.configure(text=f"Could not save: {e}")

    def try_trigger(self):
        """Show what the triggers (as edited here, not yet saved) would do with a line."""
        line = self.trig_try.get().strip()
        hits = self.T.TriggerSet(self.trig_list).match(line) if line else []
        if not line:
            self.trig_result.configure(text="")
        elif not hits:
            self.trig_result.configure(text="No trigger matches that line.")
        else:
            self.trig_result.configure(text="  |  ".join(
                f"{t['name']}: shows \"{shown}\"" + (f", says \"{say}\"" if say else "")
                for t, shown, say in hits))

    def _trigger_dialog(self, t):
        """Edit one trigger (a dict, changed in place). True if the user pressed OK."""
        top = tk.Toplevel(self.top)
        top.title("Trigger")
        top.configure(bg=P["bg"])
        top.transient(self.top)
        f = ttk.Frame(top, padding=12)
        f.pack(fill="both", expand=True)
        v = {"name": tk.StringVar(value=t["name"]), "search": tk.StringVar(value=t["search"]),
             "text": tk.StringVar(value=t["text"]), "audio": tk.StringVar(value=t["audio"]),
             "enabled": tk.BooleanVar(value=bool(t["enabled"])),
             "text_on": tk.BooleanVar(value=bool(t["text_on"])),
             "audio_on": tk.BooleanVar(value=bool(t["audio_on"]))}
        ttk.Label(f, text="Name").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=v["name"], width=48).grid(row=0, column=1, sticky="ew")
        ttk.Label(f, text="Looks for").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=v["search"], width=48).grid(row=1, column=1, sticky="ew")
        ttk.Label(f, text="A pattern (regular expression). {name} captures words.\n"
                          "Example:  ^{who} backstabs {target} for {damage} points",
                  style="Dim.TLabel", font=self.app.fonts["small"], justify="left").grid(
            row=2, column=1, sticky="w")
        ttk.Checkbutton(f, text="Show on the banner", variable=v["text_on"]).grid(
            row=3, column=0, sticky="w", pady=(8, 3))
        ttk.Entry(f, textvariable=v["text"], width=48).grid(row=3, column=1, sticky="ew",
                                                            pady=(8, 3))
        ttk.Checkbutton(f, text="Say out loud", variable=v["audio_on"]).grid(
            row=4, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=v["audio"], width=48).grid(row=4, column=1, sticky="ew")
        ttk.Checkbutton(f, text="Trigger is on", variable=v["enabled"]).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 3))
        err = ttk.Label(f, text="", foreground="#e07a5f")
        err.grid(row=6, column=0, columnspan=2, sticky="w")
        done = {"ok": False}

        def ok():
            problem = self.T.pattern_error(v["search"].get())
            if problem:
                err.configure(text=problem)
                return
            t.update(name=v["name"].get().strip() or "Trigger", search=v["search"].get().strip(),
                     text=v["text"].get(), audio=v["audio"].get(),
                     enabled=bool(v["enabled"].get()), text_on=bool(v["text_on"].get()),
                     audio_on=bool(v["audio_on"].get()))
            done["ok"] = True
            top.destroy()
        row = ttk.Frame(f)
        row.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(row, text="Cancel", command=top.destroy).pack(side="right")
        ttk.Button(row, text="OK", command=ok).pack(side="right", padx=6)
        f.columnconfigure(1, weight=1)
        top.grab_set()
        top.wait_window()
        return done["ok"]

    def _nums(self, parent, specs, row):
        for key, text, typ, (lo, hi) in specs:
            ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
            var = tk.StringVar(value=str(self.app.settings[key]))
            self.vars[key] = (var, typ, lo, hi)
            ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi,
                        increment=0.05 if typ == "float" else 1, width=8).grid(
                row=row, column=1, sticky="w")
            row += 1
        return row

    def _bools(self, parent, specs, row):
        for key, text in specs:
            current = self.app.ghost_enabled() if key == "ghost_under_mouse" \
                else bool(self.app.settings[key])
            var = tk.BooleanVar(value=current)
            self.vars[key] = var
            ttk.Checkbutton(parent, text=text, variable=var).grid(row=row, column=0, columnspan=2,
                                                                  sticky="w", pady=1)
            row += 1
        return row

    def _engine_choice(self):
        v = self.str_vars["tts_engine"].get()
        return "auto" if v == self.engine_labels[0] else v

    def test_voice(self):
        """Speak a sample with the engine chosen here (not yet saved), in the
        background, and report what the speech program actually said."""
        import threading
        from ..speech import Speaker
        sp = Speaker(self.vars["tts_command"].get().strip(), self._engine_choice())
        self.test_label.configure(text="Speaking...")
        self._test_result = None

        def work():
            self._test_result = sp.speak("Spirit of Wolf faded on Bob")
        threading.Thread(target=work, daemon=True).start()
        self.top.after(150, self._poll_test, 0)

    def _poll_test(self, n):
        if self._test_result is None and n < 200:
            self.top.after(150, self._poll_test, n + 1)
            return
        ok, detail = self._test_result or (False, "timed out")
        self.test_label.configure(text=(
            ("It ran: " + detail + ". If you heard nothing, choose a different voice above and "
             "test again.") if ok else ("It did not work: " + detail)))

    def _show_clip_names(self):
        for kind, lab in self.clip_labels.items():
            path = self.clip_paths.get(kind)
            lab.configure(text=os.path.basename(path) if path else "built-in")

    def play_clip(self, kind):
        path = self.clip_paths.get(kind) or DEFAULT_CLIPS[kind]
        if not self.app.clips.play(path):
            self.app.root.bell()

    def choose_clip(self, kind):
        path = filedialog.askopenfilename(
            parent=self.top, title="Choose the sound to play",
            filetypes=[("Audio", "*.wav *.mp3 *.ogg *.flac *.m4a"), ("All files", "*")])
        if path:
            self.clip_paths[kind] = path
            self._show_clip_names()

    def default_clip(self, kind):
        self.clip_paths.pop(kind, None)
        self._show_clip_names()

    def browse(self):
        start = self.vars["eq_dir"].get() or os.path.expanduser("~")
        d = filedialog.askdirectory(parent=self.top, initialdir=start,
                                    title="Choose your EverQuest folder")
        if d:
            self.vars["eq_dir"].set(d)

    def save(self):
        s = self.app.settings
        old_dir = s["eq_dir"]
        s["eq_dir"] = os.path.expanduser(self.vars["eq_dir"].get().strip())
        s["tts_command"] = self.vars["tts_command"].get().strip()
        s["voice_clips"] = dict(self.clip_paths)
        s["tts_engine"] = self._engine_choice()
        s["buff_filter"] = next((k for k, t in self.filter_labels.items()
                                 if t == self.str_vars["buff_filter"].get()), "mine_and_me")
        heights = dict(s["timer_view_heights"] or {})
        for key in ("timers", "mobtimers", "respawns"):
            try:
                heights[key] = int(max(60, min(1200, float(self.str_vars["height:" + key].get()))))
            except ValueError:
                pass
        s["timer_view_heights"] = heights
        s["triggers"] = [dict(t) for t in self.trig_list]
        s["ch_chain_tag"] = self.str_vars["ch_chain_tag"].get().strip()
        s["trap_phrases"] = [n.strip() for n in self.str_vars["trap_phrases"].get().split(",")
                             if n.strip()]
        s["watch_spells"] = [n.strip() for n in self.str_vars["watch_spells"].get().split(",")
                             if n.strip()]
        s["fade_voice"] = next((k for k, t in self.fade_labels.items()
                                if t == self.str_vars["fade_voice"].get()), "voice")
        for key, v in self.vars.items():
            if key in ("eq_dir", "tts_command"):
                continue
            if isinstance(v, tuple):
                var, typ, lo, hi = v
                try:
                    val = float(var.get())
                except ValueError:
                    continue
                val = max(lo, min(hi, val))
                s[key] = val if typ == "float" else int(val)
            else:
                s[key] = bool(v.get())
        self.top.destroy()
        self.app.apply_settings(eq_changed=(s["eq_dir"] != old_dir))


__all__ = ["App", "fmt_secs"]
