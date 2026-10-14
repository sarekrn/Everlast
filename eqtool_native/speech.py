"""Text-to-speech through whatever speech program the system has.

Linux: spd-say (speech-dispatcher, preinstalled on most desktops), espeak-ng,
espeak, festival. macOS: say. Windows: the built-in System.Speech voice.
Speaking never blocks the app and never raises.
"""
import logging
import shlex
import shutil
import subprocess
import sys
import time

log = logging.getLogger(__name__)


def _ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


# Engines in the order they are tried. The ones that talk to the sound system
# directly come first; spd-say (speech-dispatcher) is last because it is often
# installed with no synthesizer behind it and then says nothing at all while
# reporting success.
ENGINES = [
    ("espeak-ng", lambda exe, t: [exe, "-s", "165", t]),
    ("espeak", lambda exe, t: [exe, "-s", "165", t]),
    ("flite", lambda exe, t: [exe, "-t", t]),
    ("festival", None),                                   # reads the text from stdin
    ("spd-say", lambda exe, t: [exe, "-w", t]),           # -w: wait until it has been spoken
    ("say", lambda exe, t: [exe, t]),                     # macOS
]
ENGINE_NAMES = [name for name, _ in ENGINES]


def clean_text(text, limit=140):
    """Make a name safe to hand to a speech program: no underscores, quotes,
    control characters or leading dashes (which a program would read as an option)."""
    t = (text or "").replace("_", " ")
    t = "".join(ch for ch in t if ch.isprintable() and ch not in "\"`<>|&;$\\")
    t = " ".join(t.split()).lstrip("-").strip()
    return t[:limit]


def install_hint(os_release=None):
    """The command that installs a computer voice on this Linux, e.g.
    'sudo apt install espeak-ng'. `os_release` is the text of /etc/os-release
    (read from the system when not given)."""
    ids = ""
    try:
        text = os_release if os_release is not None else open(
            "/etc/os-release", encoding="utf-8").read()
        for line in text.splitlines():
            if line.startswith(("ID=", "ID_LIKE=")):
                ids += " " + line.split("=", 1)[1].strip().strip('"').lower()
    except OSError:
        pass
    if any(d in ids for d in ("debian", "ubuntu", "mint", "pop")):
        return "sudo apt install espeak-ng"
    if any(d in ids for d in ("fedora", "rhel", "centos")):
        return "sudo dnf install espeak-ng"
    if any(d in ids for d in ("arch", "manjaro", "steamos", "endeavouros")):
        return "sudo pacman -S espeak-ng"
    if "suse" in ids:
        return "sudo zypper install espeak-ng"
    return "install the 'espeak-ng' package with your package manager"


class Speaker:
    """Text to speech through a program already on the system."""

    def __init__(self, custom_command="", engine="auto", which=shutil.which,
                 popen=subprocess.Popen, run=subprocess.run):
        self._which, self._popen, self._run = which, popen, run
        self._procs = []
        self._last = {}
        self.custom = ""
        self.engine = None
        self.configure(custom_command, engine)

    def configure(self, custom_command="", engine="auto"):
        """custom_command: e.g. 'piper-say {text}', which wins when set.
        engine: 'auto' (the first installed, in the order above) or an engine name."""
        self.custom = (custom_command or "").strip()
        self.engine = None
        if self.custom:
            self.engine = ("custom", None)
            return
        if sys.platform.startswith("win"):
            self.engine = ("windows", None)
            return
        order = ENGINES if engine in (None, "", "auto") else \
            [e for e in ENGINES if e[0] == engine] + [e for e in ENGINES if e[0] != engine]
        for name, build in order:
            exe = self._which(name)
            if exe:
                self.engine = (exe, build)
                return

    def installed(self):
        """Names of the speech programs found on this system."""
        if sys.platform.startswith("win"):
            return ["windows"]
        return [name for name in ENGINE_NAMES if self._which(name)]

    @property
    def available(self):
        return self.engine is not None

    @property
    def name(self):
        if not self.engine:
            return ""
        return self.engine[0].rsplit("/", 1)[-1]

    @property
    def description(self):
        return f"Voice: {self.name}" if self.engine else "No speech program found"

    def command_for(self, text):
        """(command list, stdin text or None) to speak `text`, or None."""
        text = clean_text(text)
        if not text or not self.engine:
            return None
        exe, build = self.engine
        if exe == "custom":
            parts = shlex.split(self.custom)
            cmd = [a.replace("{text}", text) for a in parts]
            if not any("{text}" in a for a in parts):
                cmd.append(text)
            return cmd, None
        if exe == "windows":
            return ["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Speech; "
                    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("
                    + _ps_quote(text) + ")"], None
        if build is None:                       # festival
            return [exe, "--tts"], text
        return build(exe, text), None

    def say(self, text):
        """Start speaking and return at once (no way to learn how it went)."""
        built = self.command_for(text)
        if not built:
            return False
        now = time.monotonic()
        key = clean_text(text)
        if now - self._last.get(key, -99) < 1.5:  # don't stutter on repeats
            return False
        self._last[key] = now
        self._procs = [p for p in self._procs if p.poll() is None]
        cmd, stdin_text = built
        try:
            p = self._popen(cmd, stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=not sys.platform.startswith("win"))
            if stdin_text:
                p.stdin.write(stdin_text.encode())
                p.stdin.close()
            self._procs.append(p)
            return True
        except Exception:
            log.exception("speech failed")
            return False

    def speak(self, text, timeout=20.0):
        """Speak and wait until it is done. Returns (ok, detail); `detail` carries
        whatever the program said, so a broken setup can be diagnosed."""
        built = self.command_for(text)
        if not built:
            return False, ("no speech program found; " + install_hint()) if not self.engine \
                else "nothing to say"
        cmd, stdin_text = built
        try:
            r = self._run(cmd, input=stdin_text.encode() if stdin_text else None,
                          capture_output=True, timeout=timeout,
                          start_new_session=not sys.platform.startswith("win"))
        except FileNotFoundError:
            return False, f"{cmd[0]} was not found"
        except subprocess.TimeoutExpired:
            return False, f"{self.name} did not finish within {timeout:g} s"
        except Exception as e:
            log.exception("speech failed")
            return False, f"{self.name}: {e}"
        err = (r.stderr or b"").decode("utf-8", "replace").strip()[:200]
        if r.returncode != 0:
            return False, f"{self.name} failed (exit {r.returncode})" + (f": {err}" if err else "")
        return True, f"spoken with {self.name}" + (f" (it also said: {err})" if err else "")


# ---------------------------------------------------------------- sound clips

import os  # noqa: E402

SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
DEFAULT_CLIPS = {
    "charm": os.path.join(SOUNDS_DIR, "charm_break.wav"),
    "root": os.path.join(SOUNDS_DIR, "root_break.wav"),
    "my_buff": os.path.join(SOUNDS_DIR, "buff_faded.wav"),   # your buff on a player faded
}

# (program, args before the file, plays formats other than WAV?)
PLAYERS = [
    ("pw-play", [], False),                                   # PipeWire
    ("paplay", [], False),                                    # PulseAudio
    ("aplay", ["-q"], False),                                 # ALSA
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"], True),
    ("mpv", ["--no-video", "--really-quiet"], True),
    ("gst-play-1.0", ["-q", "--no-interactive"], True),       # GStreamer (GNOME)
    ("mpg123", ["-q"], True),                                 # MP3 only, but common
    ("cvlc", ["--play-and-exit", "--quiet"], True),
    ("afplay", [], True),                                     # macOS
]


class ClipPlayer:
    """Plays short audio files (your recorded announcements) without blocking."""

    def __init__(self, which=shutil.which, popen=subprocess.Popen):
        self._which, self._popen = which, popen
        self._procs = []
        self._last = {}
        self.wav_players, self.any_players = [], []
        self._mpg123 = None
        for name, args, any_format in PLAYERS:
            exe = which(name)
            if not exe:
                continue
            if name == "mpg123":        # MP3 only
                self._mpg123 = (exe, args)
                continue
            self.wav_players.append((exe, args))
            if any_format:
                self.any_players.append((exe, args))

    @property
    def available(self):
        return bool(self.wav_players) or sys.platform.startswith("win")

    @property
    def description(self):
        if self.wav_players:
            return "Clips play with " + self.wav_players[0][0].rsplit("/", 1)[-1]
        if sys.platform.startswith("win"):
            return "Clips play with Windows sound"
        return "No audio player found"

    def _player_for(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".wav":
            return self.wav_players[0] if self.wav_players else None
        if self.any_players:
            return self.any_players[0]
        if ext == ".mp3" and self._mpg123:
            return self._mpg123
        return None

    def can_play(self, path):
        if not path or not os.path.isfile(path):
            return False
        if sys.platform.startswith("win"):
            return path.lower().endswith(".wav")
        return self._player_for(path) is not None

    def play(self, path, wait=False, timeout=10.0):
        """Play a clip. wait=True returns only when it has finished (for the
        announcer, which plays things one after another)."""
        if not self.can_play(path):
            return False
        now = time.monotonic()
        if now - self._last.get(path, -99) < (0.0 if wait else 1.0):
            return False
        self._last[path] = now
        try:
            if sys.platform.startswith("win"):
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | (0 if wait else winsound.SND_ASYNC))
                return True
            self._procs = [p for p in self._procs if p.poll() is None]
            exe, args = self._player_for(path)
            proc = self._popen([exe, *args, path], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
            self._procs.append(proc)
            if wait:
                try:
                    proc.wait(timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
            return True
        except Exception:
            log.exception("could not play %s", path)
            return False
