"""Everlast by Sarekrn: entry point.

    python3 -m eqtool_native                 # start the overlay app
    python3 -m eqtool_native --replay LOG    # run a log through the parser, print results
"""
import argparse
import logging
import logging.handlers
import sys
import time

from . import __version__
from .settings import Settings, cache_dir


def setup_logging(verbose=False):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    try:
        fh = logging.handlers.RotatingFileHandler(
            cache_dir() / "eqtool.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
    except OSError:
        pass
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(sh)


def replay(args):
    from .engine import Engine
    from .logwatch import parse_line
    from .spells import SpellDB

    settings = Settings(args.settings) if args.settings else Settings()
    if args.level:
        settings["level_override"] = args.level
    spells = SpellDB()
    path = args.spells or SpellDB.find(settings["eq_dir"])
    if path:
        spells.load(path)
    else:
        print("note: no spells_us.txt given/found; spell timers disabled")
    eng = Engine(settings, spells)
    last = time.time()
    with open(args.replay, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = parse_line(line)
            if not p:
                continue
            last = p[0]
            eng.handle(*p)
            eng.tick(last)
            while eng.events:
                kind, data = eng.events.popleft()
                if kind != "loc" or args.verbose:
                    print(f"[event] {kind}: {data}")
    print(f"\nzone={eng.zone!r} loc={eng.loc} level={eng.level} handler_errors={eng.errors}")
    print("\nTimers active at end of log:")
    for t in eng.timers.active(last):
        rem = t.end - last
        print(f"  {t.kind:7} {t.target:20} {t.label:32} {int(rem // 60)}:{int(rem % 60):02d}")
    print("\nFights:")
    for fight in eng.dps.fights()[-args.fights:]:
        print(f"  {fight.target} ({int(fight.duration)}s, total {fight.total}, taken {fight.taken})")
        for name, dmg, dps, pct in fight.rows():
            print(f"      {name:20} {dmg:7} {dps:7.1f} dps {pct:5.1f}%")
    return 0


def spell_info(args):
    from .spells import (F_CLASSES, F_DUR, F_DUR_FORMULA, F_GOOD, F_TARGET, N_CLASSES,
                         SpellDB, skip_spell)
    settings = Settings(args.settings) if args.settings else Settings()
    path = args.spells or SpellDB.find(settings["eq_dir"])
    if not path:
        print("No spells_us.txt found. Pass --spells /path/to/spells_us.txt")
        return 1
    db = SpellDB().load(path)
    level = args.level or int(settings.num("level_override", 0)) or 60
    want = args.spell_info.lower()
    found = 0
    with open(path, encoding="latin-1") as f:
        for raw in f:
            fld = raw.rstrip("\r\n").split("^")
            if len(fld) < 2 or fld[1].strip().lower() != want:
                continue
            found += 1
            sid = int(fld[0])
            sp = db.spells.get(sid)
            classes = fld[F_CLASSES:F_CLASSES + N_CLASSES]
            print(f"id {sid}  name {fld[1]!r}  ({len(fld)} columns)")
            print(f"  duration formula [16] = {fld[F_DUR_FORMULA]}   duration [17] = {fld[F_DUR]}")
            print(f"  cast time [13] = {fld[13]}   recast [15] = {fld[15]}   "
                  f"beneficial [83] = {fld[F_GOOD] if len(fld) > F_GOOD else '?'}   "
                  f"target type [98] = {fld[F_TARGET] if len(fld) > F_TARGET else '?'}")
            print(f"  class levels [104..117] = {' '.join(classes)}")
            print(f"  on you  [6] = {fld[6]!r}")
            print(f"  on other[7] = {fld[7]!r}")
            print(f"  fades   [8] = {fld[8]!r}")
            if sp:
                secs = sp.duration_seconds(level)
                fixed = (settings["spell_durations"] or {}).get(sp.name.lower())
                print(f"  -> at level {level}: {secs // 60:.0f}m {secs % 60:02.0f}s"
                      f" (caster level used: {sp.caster_level(level)})"
                      + (f"; your correction: {fixed}s" if fixed else "")
                      + ("; not tracked (teleport/GM/NPC-only type)"
                         if skip_spell(sp.name, sp.target_type) else ""))
            print()
    if not found:
        print(f"No spell named {args.spell_info!r} in {path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="eqtool-native", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", metavar="LOG", help="parse a log file and print what was detected")
    ap.add_argument("--spells", metavar="FILE", help="spells_us.txt to use with --replay")
    ap.add_argument("--level", type=int, default=0, help="character level for --replay")
    ap.add_argument("--fights", type=int, default=5, help="how many fights to print in --replay")
    ap.add_argument("--spell-info", metavar="NAME",
                    help="show what spells_us.txt says about a spell and the timer it gives")
    ap.add_argument("--import-eqtool-maps", metavar="EXE",
                    help="copy the zone maps embedded in an EQTool.exe into ~/.local/share")
    ap.add_argument("--settings", metavar="FILE", help="use an alternate settings.json")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"Everlast {__version__}")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)

    if args.replay:
        return replay(args)
    if args.spell_info:
        return spell_info(args)
    if args.import_eqtool_maps:
        from .dotnet_resources import DotNetError, extract_eqtool_maps
        from .maps import user_maps_dir
        try:
            n = extract_eqtool_maps(args.import_eqtool_maps, user_maps_dir())
        except (OSError, DotNetError) as e:
            print(f"Could not read {args.import_eqtool_maps}: {e}", file=sys.stderr)
            return 1
        print(f"Imported {n} map files into {user_maps_dir()}")
        return 0

    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(
            "Tk for Python isn't installed. Install it with your package manager:\n"
            "  Debian/Ubuntu/Mint:  sudo apt install python3-tk\n"
            "  Fedora:              sudo dnf install python3-tkinter\n"
            "  Arch/Manjaro:        sudo pacman -S tk\n"
            "  openSUSE:            sudo zypper install python3-tk",
            file=sys.stderr)
        return 1
    from .ui.app import App
    App(Settings(args.settings) if args.settings else Settings()).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
