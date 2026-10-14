"""Settings storage (XDG paths, atomic writes, never crashes on a bad file)."""
import copy
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)
APP = "eqtool-native"

SETTINGS_VERSION = 2

DEFAULTS = {
    "settings_version": SETTINGS_VERSION,   # bumped when a saved setting needs resetting once
    "eq_dir": "",                 # EverQuest install dir (inside your Wine prefix)
    "log_file": "",               # optional: force a specific eqlog_*.txt
    "character_levels": {},       # character (lower) -> last level seen in the log
    "level_override": 0,          # 0 = auto-detect from log
    "always_on_top": True,
    "tray_icon": True,            # Everlast icon in the system tray
    "minimize_to_tray": True,     # minimizing leaves only the tray icon (no taskbar button)
    "start_minimized": False,     # start hidden in the tray
    "overlays_locked": False,     # overlays borderless + click-through
    "clickthrough_windows": {},   # per window: pass clicks when locked (map defaults to no)
    "ghost_under_mouse": None,       # hide a locked overlay while the mouse is over it; None = auto (on for Wayland)
    "overlay_refresh_seconds": 0,    # re-map the overlays this often (0 = only when you press Refresh overlays)
    "timer_view_heights": {},        # per timer window: how tall its list is, in px (the corner grip sets it)
    "buff_filter": "mine_and_me",    # Buff Timers shows: all | mine_and_me | mine
    "shrink_locked_overlays": True,  # in play mode, shrink timer windows to just their bars
    "solid_timers": True,         # timer bars/text always opaque; the slider fades only the background
    "hide_borders": False,        # overlays without title bars (still clickable)
    "opacity": 0.92,
    "window_opacity": {},         # per-window opacity (Ctrl+scroll), overrides "opacity"
    "font_size": 10,
    "fight_timeout": 20,          # seconds of no damage before a fight ends
    "show_recast": True,
    "min_recast": 10,             # only show recast timers >= this many seconds
    "auto_spawn_timers": True,    # start a respawn timer for every kill
    "spawn_needs_experience": True,  # ...but only kills that give experience ("You gain experience!!")
    "default_spawn_seconds": 400, # 6:40, the classic P99 default
    "mob_timers_debuffs": True,   # your debuffs on mobs go in the Mob Timers window (else with the buffs)
    "spawn_rules": {},            # mob name (lower) -> respawn seconds
    "spell_durations": {},        # spell name (lower) -> your corrected seconds
    "map_negate_loc": True,       # Brewall-style maps store -/loc coordinates
    "map_labels": True,
    "map_mob_names": True,        # the red (hostile NPC) labels on the map
    "map_follow": True,
    "map_level_zones": {},        # zone short name -> {"mode": all|dim|hide, "height": z units}
    "map_auto_show": True,        # open the map window when you zone
    "zone_aliases": {},           # "Zone Long Name" -> "mapfile"
    # Alert banner
    "alerts_enabled": True,
    "alert_charm": True,          # CHARM BREAK
    "alert_root": True,           # ROOT BREAK
    "alert_worn_off": False,      # any other "Your X spell has worn off."
    "alert_timers": True,         # your buffs, debuffs, custom timers ending
    "alert_spawns": False,        # a mob you killed is due to respawn (can be a lot of them)
    "alert_fizzle": True,         # "Your spell fizzles!"
    "triggers": None,             # custom triggers (None = EQTool's built-in ones)
    "alert_triggers": True,       # show the banner for custom triggers that ask for it
    "tts_triggers": True,         # ...and say the ones that ask for it
    "alert_roar": True, "tts_roar": True,         # Dragon Roar: out + countdown
    "alert_enrage": True, "tts_enrage": True,     # "<mob> has become ENRAGED."
    "alert_dt": True, "tts_dt": True,             # Death Touch (Fright, Dread)
    "raid_aoe_timers": True,      # timers for the raid mobs' zone-wide spells
    "alert_con": True,            # /consider: KOS or SAFE
    "boat_timers": True,          # boat arrival timers from the boat captains' announcements
    "alert_boat": True, "tts_boat": True,          # a boat is arriving
    "ringwar_timers": True,       # Ring War round timers (Seneschal Aldikar)
    "alert_ringwar": True, "tts_ringwar": True,    # a Ring War round / break starts
    "ch_chain": True,             # follow "001 CH -- Target" calls in chat
    "ch_chain_tag": "",           # only calls that start with this tag (blank = all)
    "alert_ch_warn": True, "tts_ch_warn": True,    # the slot before yours was called
    "alert_trap": True,           # pansy trap tripped (Plane of Mischief)
    "alert_invite": True,         # "<Name> invites you to join a group."
    "alert_interrupt": True,      # "Your spell is interrupted."
    "alert_watch": True,          # a spell on the watch list wears off
    "alert_self_fades": True,     # a buff on YOU fades
    "alert_my_buffs": True,       # a buff YOU cast on another player fades
    "alert_timers_others": False, # other people's buffs seen on other players ending
    "alert_recast": False,        # "X ready" when a long recast finishes
    "alert_font_size": 48,
    "alert_color": "#FFD400",
    "alert_seconds": 5,
    "alert_opacity": 0.85,
    # Text to speech
    "tts_enabled": True,
    "tts_charm_root": True,       # say "Charm break" / "Root break"
    "tts_fizzle": True,           # say "Spirit of Wolf fizzled"
    "tts_trap": True,
    "tts_con": True,              # say KOS / Safe when you /consider a mob
    "tts_invite": True,
    "tts_interrupt": True,        # say "Spirit of Wolf interrupted"
    "tts_watch": True,            # say "Curse of the Spirits wore off"
    "tts_self_fades": True,       # say "Spirit of Wolf faded" when a buff on you fades
    "tts_my_buffs": True,         # say "Spirit of Wolf faded on Bob"
    "fade_voice": "voice",        # how buff fades are announced: voice | clip | both
    "tts_engine": "auto",         # computer voice program: auto, or one of speech.ENGINE_NAMES
    "tts_timers": False,          # also speak timer endings
    "trap_phrases": [],           # extra log text that means a trap went off
    "watch_spells": ["Curse of the Spirits"],   # always announce when these wear off
    "tts_command": "",
    "voice_clips_enabled": True,  # play recorded clips for charm/root breaks and buff fades
    "voice_clips": {},            # "charm"/"root"/"my_buff" -> your own file (empty = built-in)            # custom speech command, e.g. "piper-say {text}"
    "windows": {},                # window name -> {"geometry": str, "visible": bool}
}


def _xdg(var, fallback):
    base = os.environ.get(var) or os.path.join(os.path.expanduser("~"), fallback)
    p = Path(base) / APP
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir():
    return _xdg("XDG_CONFIG_HOME", ".config")


def cache_dir():
    return _xdg("XDG_CACHE_HOME", ".cache")


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path else config_dir() / "settings.json"
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for k, v in loaded.items():
                    # Ignore values whose type doesn't match the default (hand-edit typos).
                    if k in DEFAULTS and DEFAULTS[k] is not None and not isinstance(
                        v, type(DEFAULTS[k])
                    ) and not (isinstance(DEFAULTS[k], float) and isinstance(v, int)):
                        log.warning("settings: ignoring %s=%r (bad type)", k, v)
                        continue
                    self.data[k] = v
                self._migrate(loaded)
        except Exception:
            log.exception("settings file unreadable; starting from defaults")
            try:
                self.path.rename(self.path.with_suffix(".bad"))
            except OSError:
                pass

    def _migrate(self, loaded):
        """One-time resets for settings saved by an older version."""
        try:
            version = int(loaded.get("settings_version", 0) or 0)
        except (TypeError, ValueError):
            version = 0
        if version < 2:
            # Hover-hide was switched on for everyone while clicks could not pass through
            # (a bug, since fixed). Back to automatic: on only where it is needed (Wayland).
            self.data["ghost_under_mouse"] = None
        self.data["settings_version"] = SETTINGS_VERSION

    def save(self):
        try:
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception:
            log.exception("could not save settings")

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def num(self, key, default=0.0):
        try:
            return float(self.data.get(key, DEFAULTS.get(key, default)))
        except (TypeError, ValueError):
            return float(default)


_SKIP_DIRS = {"windows", "proc", "node_modules", "__pycache__", "dosdevices", "Temp", "temp"}


def autodetect_eq_dir(timeout=4.0):
    """Look for an EverQuest install (eqgame.exe) in common Wine/Lutris/Steam locations."""
    home = Path.home()
    roots = [
        (home / ".wine", 7),
        (home / "Games", 8),
        (home / ".local/share/lutris", 8),
        (home / ".local/share/bottles", 9),
        (home / ".var/app", 10),
        (home / ".steam/steam/steamapps/compatdata", 9),
        (home / ".local/share/Steam/steamapps/compatdata", 9),
        (home / ".PlayOnLinux", 8),
        (home, 4),
    ]
    deadline = time.monotonic() + timeout
    for root, max_depth in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if time.monotonic() > deadline:
                return None
            lower = {f.lower() for f in filenames}
            if "eqgame.exe" in lower:
                return dirpath
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_DIRS and not (root == home and d.startswith("."))
            ]
    return None
