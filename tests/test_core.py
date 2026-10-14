import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eqtool_native import maps, mobinfo  # noqa: E402
from eqtool_native.engine import Engine  # noqa: E402
from eqtool_native.logwatch import LogWatcher, parse_line  # noqa: E402
from eqtool_native.settings import Settings  # noqa: E402
from eqtool_native.spells import SpellDB, duration_ticks  # noqa: E402


def spell_line(sid, name, on_you="", on_other="", fades="", cast=3000, recast=0,
               formula=3, dur=270, good=1, class_lvl=9, target=5):
    f = [""] * 130
    f[98] = str(target)
    f[0], f[1], f[6], f[7], f[8] = str(sid), name, on_you, on_other, fades
    f[13], f[15], f[16], f[17], f[83] = str(cast), str(recast), str(formula), str(dur), str(good)
    for i in range(104, 119):
        f[i] = "255"
    f[104] = str(class_lvl)
    return "^".join(f)


SPELLS = "\n".join([
    spell_line(278, "Spirit of Wolf", "You feel the spirit of wolf enter you.",
               " feels the spirit of wolf enter them.", "The spirit of wolf leaves you.",
               formula=3, dur=360),
    spell_line(39, "Quickness", "You feel quick.", " feels quick.", "You slow down.", dur=30,
               formula=2),
    spell_line(40, "Alacrity", "You feel quick.", " feels quick.", "You slow down.", dur=110,
               formula=3, class_lvl=24),
    spell_line(230, "Root", "Your feet adhere to the ground.", " adheres to the ground.",
               "Your feet come free.", formula=1, dur=5, good=0, recast=0),
    spell_line(95, "Armor of Faith", "You feel armored by faith.",
               "'s skin shimmers with faith.", "", formula=3, dur=270),
    spell_line(12, "Complete Heal", "You feel much better.", "", "", formula=0, dur=0),
    spell_line(500, "Harmshield", "You feel invulnerable.", " is protected.", "",
               formula=5, dur=3, recast=4320000),
])


class MapMobLabelTests(unittest.TestCase):
    def test_red_labels_are_mobs(self):
        from eqtool_native import maps
        self.assertTrue(maps.is_mob_label((240, 0, 0)))
        self.assertTrue(maps.is_mob_label((127, 0, 0)))
        for rgb in ((0, 0, 240), (0, 0, 0), (0, 127, 0), (127, 64, 0), (255, 255, 255)):
            self.assertFalse(maps.is_mob_label(rgb))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        with open(os.path.join(d, "spells_us.txt"), "w", encoding="latin-1") as f:
            f.write(SPELLS + "\n")
        self.settings = Settings(os.path.join(d, "settings.json"))
        self.settings["level_override"] = 20
        self.spells = SpellDB().load(os.path.join(d, "spells_us.txt"))
        self.eng = Engine(self.settings, self.spells)

    def tearDown(self):
        self.tmp.cleanup()

    def feed(self, *lines, start=1_000_000.0):
        ts = start
        for ln in lines:
            if isinstance(ln, (int, float)):
                ts += ln
                continue
            self.eng.handle(ts, ln)
            self.eng.tick(ts)
        return ts


class SharedSpellDBTest(unittest.TestCase):
    def test_engine_shares_empty_db_loaded_later(self):
        with tempfile.TemporaryDirectory() as d:
            db = SpellDB()
            eng = Engine(Settings(os.path.join(d, "s.json")), db)
            open(os.path.join(d, "sp.txt"), "w").write(SPELLS)
            db.load(os.path.join(d, "sp.txt"))
            eng.handle(1.0, "You feel the spirit of wolf enter you.")
            self.assertEqual(len(eng.timers.timers), 1)


class ParseTests(unittest.TestCase):
    def test_parse_line(self):
        ts, msg = parse_line("[Tue Sep 29 09:05:03 2026] You begin casting Root.\r\n")
        self.assertEqual(msg, "You begin casting Root.")
        self.assertGreater(ts, 0)
        self.assertIsNone(parse_line("garbage"))
        self.assertIsNone(parse_line("[Xxx Foo 99 99:99:99 2026] bad"))

    def test_durations(self):
        self.assertEqual(duration_ticks(3, 360, 20), 360)
        self.assertEqual(duration_ticks(1, 5, 20), 5)
        self.assertEqual(duration_ticks(1, 50, 20), 10)
        self.assertEqual(duration_ticks(0, 100, 50), 0)
        self.assertEqual(duration_ticks(2, 30, 20), 12)


class SpellTimerTests(Base):
    def test_self_buff_and_fade(self):
        self.feed("You begin casting Spirit of Wolf.", 3, "You feel the spirit of wolf enter you.")
        t = list(self.eng.timers.timers.values())
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0].label, t[0].target, t[0].kind), ("Spirit of Wolf", "You", "buff"))
        self.assertAlmostEqual(t[0].end - t[0].start, 360 * 6)
        self.feed("The spirit of wolf leaves you.")
        self.assertEqual(len(self.eng.timers.timers), 0)

    def test_other_target_multiword(self):
        self.feed("You begin casting Root.", 2, "a gnoll pup adheres to the ground.")
        t = list(self.eng.timers.timers.values())[0]
        self.assertEqual((t.target, t.kind, t.label), ("a gnoll pup", "debuff", "Root"))
        self.assertAlmostEqual(t.end - t.start, 30 + 6)     # a debuff on a mob gets the extra tick
        self.feed("a gnoll pup has been slain by Bob!")
        self.assertEqual(len(self.eng.timers.timers), 0)

    def test_possessive(self):
        self.feed("Soandso's skin shimmers with faith.")
        t = list(self.eng.timers.timers.values())[0]
        self.assertEqual((t.target, t.label), ("Soandso", "Armor of Faith"))

    def test_ambiguous_resolved_by_own_cast(self):
        self.feed("You begin casting Alacrity.", 3, "Bob feels quick.")
        t = list(self.eng.timers.timers.values())[0]
        self.assertEqual(t.label, "Alacrity")

    def test_ambiguous_without_cast_marked(self):
        self.feed("You feel quick.")
        t = list(self.eng.timers.timers.values())[0]
        self.assertTrue(t.label.endswith("(?)"))

    def test_no_timer_for_instant_heal(self):
        self.feed("You feel much better.")
        self.assertEqual(len(self.eng.timers.timers), 0)

    def test_recast_timer_and_interrupt(self):
        self.feed("You begin casting Harmshield.", 4)
        self.eng.tick(1_000_004.0)
        rec = [t for t in self.eng.timers.timers.values() if t.kind == "recast"]
        self.assertEqual(len(rec), 1)
        self.eng.timers.clear()
        self.feed("You begin casting Harmshield.", 1, "Your spell is interrupted.", 5, start=2e6)
        self.eng.tick(2e6 + 10)
        self.assertFalse([t for t in self.eng.timers.timers.values() if t.kind == "recast"])

    def test_zone_clears_debuffs_keeps_buffs(self):
        self.feed("You feel the spirit of wolf enter you.", "a bat adheres to the ground.",
                  "You have entered East Commonlands.")
        kinds = {t.kind for t in self.eng.timers.timers.values()}
        self.assertEqual(kinds, {"buff"})
        self.assertEqual(self.eng.zone, "East Commonlands")

    def test_death_clears_self_buffs(self):
        self.feed("You feel the spirit of wolf enter you.", "You have been slain by a bat!")
        self.assertEqual(len(self.eng.timers.timers), 0)


class DpsTests(Base):
    def test_fight(self):
        self.feed(
            "You slash a gnoll pup for 12 points of damage.", 1,
            "Bob hits a gnoll pup for 8 points of damage.", 1,
            "a gnoll pup hits YOU for 3 points of damage.", 1,
            "a gnoll pup was hit by non-melee for 20 points of damage.", 1,
            "a gnoll pup hits Bob for 4 points of damage.", 1,
            "You have slain a gnoll pup!")
        f = self.eng.dps.fights()[-1]
        self.assertEqual(f.damage, {"You": 12, "Bob": 8, "Non-melee": 20})
        self.assertEqual(f.taken, 7)
        self.assertTrue(f.ended)
        self.assertEqual(f.duration, 5)  # 0s..4s inclusive

    def test_nuke_attributed_while_casting(self):
        self.feed("You begin casting Root.", 1,
                  "a bat was hit by non-melee for 50 points of damage.")
        self.assertEqual(self.eng.dps.fights()[-1].damage, {"You": 50})

    def test_timeout_splits_fights(self):
        self.feed("You hit a rat for 5 points of damage.", 60,
                  "You hit a rat for 5 points of damage.")
        self.assertEqual(len(self.eng.dps.fights()), 2)

    def test_spawn_timer_rule(self):
        self.settings["spawn_rules"] = {"fippy darkpaw": 1200}
        self.feed("Your Location is 100.5, -200.25, 3.0", "Fippy Darkpaw has been slain by Bob!")
        t = [t for t in self.eng.timers.timers.values() if t.kind == "spawn"][0]
        self.assertIn("Fippy Darkpaw", t.label)
        self.assertAlmostEqual(t.end - t.start, 1200)


class MiscEngineTests(Base):
    def test_loc_con_level(self):
        self.eng.character = "Tester"
        self.settings["level_override"] = 0
        self.feed("Your Location is -10.00, 20.50, 1.00",
                  "a gnoll pup regards you indifferently -- what would you like your tombstone to say?",
                  "[34 Enchanter] Tester (Erudite) <Guild>",
                  "You have entered an area where levitation effects do not function.")
        self.assertEqual(self.eng.loc, (-10.0, 20.5, 1.0))
        self.assertEqual(self.eng.last_con, "a gnoll pup")
        self.assertEqual(self.eng.level, 34)
        self.assertIsNone(self.eng.zone)
        self.feed("You have gained a level! Welcome to level 35!")
        self.assertEqual(self.eng.level, 35)

    def test_garbage_never_raises(self):
        for junk in ["", " ", "\x00\x01", "You begin casting .", "x" * 5000,
                     "Your Location is a, b, c", "You have entered .", " hits  for 5 points of damage."]:
            self.eng.handle(1.0, junk)
        self.assertEqual(self.eng.errors, 0)


class ConAlertTests(Base):
    def alerts(self):
        out = []
        while self.eng.events:
            k, d = self.eng.events.popleft()
            if k == "alert":
                out.append((d.kind, d.text, d.say))
        return out

    def test_kos_and_safe(self):
        kos = ["a gnoll glares at you threateningly -- looks like you could take a gnoll.",
               "a gnoll scowls at you, ready to attack -- you would have a good chance."]
        safe = ["a gnoll regards you as an ally", "a gnoll looks upon you warmly",
                "a gnoll kindly considers you", "a gnoll judges you amiably",
                "a gnoll regards you indifferently", "a gnoll looks your way apprehensively",
                "a gnoll glowers at you dubiously"]
        for m in kos:
            self.feed(m)
            self.assertEqual(self.alerts(), [("con", "KOS", "K O S")], m)
        for m in safe:
            self.feed(m)
            self.assertEqual(self.alerts(), [("con", "SAFE", "Safe")], m)
        self.assertEqual(self.eng.last_con, "a gnoll")


class CharacterSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        os.makedirs(os.path.join(self.d, "Logs"))
        self.a = os.path.join(self.d, "Logs", "eqlog_Alpha_P1999Green.txt")
        self.b = os.path.join(self.d, "Logs", "eqlog_Beta_P1999Green.txt")
        for p in (self.a, self.b):
            with open(p, "w") as f:
                f.write("[Tue Oct 13 10:00:00 2026] hello\n")
        os.utime(self.a, (2_000_000_000, 2_000_000_000))
        os.utime(self.b, (1_000_000_000, 1_000_000_000))
        self.w = LogWatcher(self.d)

    def tearDown(self):
        self.w.close()
        self.tmp.cleanup()

    def write(self, p, text="[Tue Oct 13 10:00:01 2026] You hit a rat for 5 points of damage.\n", mtime=None):
        with open(p, "a") as f:
            f.write(text)
        if mtime:
            os.utime(p, (mtime, mtime))

    def test_switches_when_the_other_log_is_written(self):
        self.w.poll(now=0)
        self.assertEqual(self.w.character, "Alpha")
        self.write(self.b, mtime=2_000_000_100)
        self.w.poll(now=5)
        self.assertEqual(self.w.character, "Beta")

    def test_switches_even_if_the_new_logs_time_is_older(self):
        """A log whose modified time is not kept current still switches by growing."""
        self.w.poll(now=0)
        self.write(self.b, mtime=1_000_000_500)            # still far older than Alpha's
        self.w.poll(now=5)
        self.assertEqual(self.w.character, "Beta")

    def test_chat_in_both_logs_does_not_move_us(self):
        """Guild chat lands in every logged-in client's log; only 'You...' lines are ours."""
        self.w.poll(now=0)
        self.write(self.b, "[Tue Oct 13 10:00:02 2026] Bob tells the guild, 'hi'\n",
                   mtime=2_000_000_900)
        self.w.poll(now=5)
        self.assertEqual(self.w.character, "Alpha")

    def test_start_picks_the_log_with_personal_lines_not_the_chatty_one(self):
        self.w.close()
        self.write(self.a, "[Tue Oct 13 10:05:00 2026] Bob tells the guild, 'hi'\n",
                   mtime=2_000_000_900)             # idle character, newest file
        self.write(self.b, "[Tue Oct 13 10:04:00 2026] You have entered The Overthere.\n",
                   mtime=1_999_999_000)
        w = LogWatcher(self.d)
        w.poll(now=0)
        self.assertEqual(w.character, "Beta")
        w.close()

    def test_quiet_seconds(self):
        self.w.poll(now=0)
        self.assertGreaterEqual(self.w.quiet_seconds(now=time.monotonic() + 100), 99)
        self.write(self.a)
        self.w.poll(now=time.monotonic() + 1)
        self.assertLess(self.w.quiet_seconds(now=time.monotonic() + 2), 5)

    def test_stays_when_nothing_changes(self):
        self.w.poll(now=0)
        self.w.poll(now=5)
        self.w.poll(now=10)
        self.assertEqual(self.w.character, "Alpha")

    def test_forced_log(self):
        self.w.poll(now=0)
        self.w.force(self.b)
        self.w.poll(now=1)
        self.assertEqual(self.w.character, "Beta")
        self.write(self.a, mtime=2_000_000_900)
        self.w.poll(now=9)
        self.assertEqual(self.w.character, "Beta")         # stays on the picked log
        self.w.force("")
        self.write(self.a, mtime=2_000_001_000)
        self.w.poll(now=20)
        self.w.poll(now=30)
        self.assertEqual(self.w.character, "Alpha")

    def test_level_is_remembered_per_character(self):
        s = Settings(os.path.join(self.d, "s2.json"))
        e = Engine(s, SpellDB())
        e.switch_character("Alpha")
        e.handle(1000, "You have gained a level! Welcome to level 52!")
        e.switch_character("Beta")
        self.assertEqual(e.detected_level, 0)
        e.handle(1000, "You have gained a level! Welcome to level 33!")
        e.switch_character("Alpha")
        self.assertEqual(e.detected_level, 52)
        e.switch_character("Beta")
        self.assertEqual(e.level, 33)

    def test_engine_forgets_the_old_character(self):
        s = Settings(os.path.join(self.d, "s.json"))
        e = Engine(s, SpellDB())
        e.zone = "Old Zone"
        e.timers.add("Spirit of Wolf", "You", 100, "buff", 0)
        e.sieves["a"] = {"name": "a", "n": 1, "first": 0, "last": 0}
        e.switch_character("Beta")
        self.assertEqual((e.character, e.zone, len(e.timers.timers), e.sieves),
                         ("Beta", None, 0, {}))


class SieveTests(Base):
    def test_counts_every_sieve_by_mob(self):
        self.feed("a lich staggers with mental anguish.", 3,
                  "a lich staggers with mental anguish.", 2,
                  "a lich staggers with mental anguish.",
                  "Fright staggers with mental anguish.")
        rows = {r[0]: r[1] for r in self.eng.sieve_counts()}
        self.assertEqual(rows, {"a lich": 3, "Fright": 1})

    def test_count_goes_when_the_mob_dies_and_restarts(self):
        self.feed("a lich staggers with mental anguish.", "a lich staggers with mental anguish.",
                  "a lich has been slain by Tank!")
        self.assertEqual(self.eng.sieve_counts(), [])
        self.feed("a lich staggers with mental anguish.")
        self.assertEqual([r[1] for r in self.eng.sieve_counts()], [1])

    def test_you_slay_and_died_forms(self):
        self.feed("a gnoll staggers with mental anguish.", "You have slain a gnoll!",
                  "an orc staggers with mental anguish.", "an orc died.")
        self.assertEqual(self.eng.sieve_counts(), [])

    def test_other_mob_unaffected_by_a_kill(self):
        self.feed("a gnoll staggers with mental anguish.", "an orc staggers with mental anguish.",
                  "an orc died.")
        self.assertEqual([r[0] for r in self.eng.sieve_counts()], ["a gnoll"])

    def test_zoning_and_idle_clear(self):
        self.feed("a gnoll staggers with mental anguish.",
                  "You have entered The Overthere.")
        self.assertEqual(self.eng.sieve_counts(), [])
        t = self.feed("a gnoll staggers with mental anguish.")
        self.eng.tick(t + 1000)
        self.assertEqual(self.eng.sieve_counts(), [])

    def test_other_lines_ignored(self):
        self.feed("a gnoll staggers.", "You stagger with mental anguish.x")
        self.assertEqual(self.eng.sieve_counts(), [])


class WatcherTests(unittest.TestCase):
    def test_tail_partial_truncate_switch(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "Logs"))
            p = os.path.join(d, "Logs", "eqlog_Tester_P1999Green.txt")
            open(p, "w").write("old line\n")
            w = LogWatcher(d)
            self.assertEqual(w.poll(0), [])  # opens at end
            self.assertEqual(w.character, "Tester")
            with open(p, "a") as f:
                f.write("line one\nline tw")
            self.assertEqual(w.poll(0.1), ["line one"])
            with open(p, "a") as f:
                f.write("o\r\n")
            self.assertEqual(w.poll(0.2), ["line two"])
            open(p, "w").write("fresh\n")  # truncated
            self.assertEqual(w.poll(0.3), ["fresh"])
            p2 = os.path.join(d, "Logs", "eqlog_Alt_P1999Green.txt")
            open(p2, "w").write("[Tue Oct 13 10:00:00 2026] Welcome to EverQuest!\n")
            os.utime(p2, (os.path.getmtime(p) + 10,) * 2)
            w.poll(5)
            self.assertEqual(w.character, "Alt")


class HistoryTests(unittest.TestCase):
    def _log(self, d, body):
        os.makedirs(os.path.join(d, "Logs"), exist_ok=True)
        p = os.path.join(d, "Logs", "eqlog_Tester_P1999Green.txt")
        with open(p, "wb") as f:
            f.write(body)
        return p

    def _eng(self, d):
        s = Settings(os.path.join(d, "s.json"))
        s["level_override"] = 0
        e = Engine(s, SpellDB())
        e.character = "Tester"
        return e

    def test_zone_found_far_back_and_no_stale_timers(self):
        from eqtool_native import logwatch
        ts = "[Tue Sep 29 20:00:00 2026] "
        filler = (ts + "Bob hits a rat for 5 points of damage.\n").encode() * 120000  # ~7 MB
        body = ((ts + "You have entered East Commonlands.\n").encode() + filler +
                (ts + "You have entered The Northern Plains of Karana.\n").encode() +
                (ts + "You have entered an area where levitation effects do not function.\n").encode() +
                filler +
                (ts + "Your Location is 100.00, -200.00, 5.00\n").encode() +
                (ts + "[25 Druid] Tester (Wood Elf)\n").encode())
        with tempfile.TemporaryDirectory() as d:
            self._log(d, body)
            w = LogWatcher(d)
            self.assertEqual(w.poll(0), [])
            hist = w.take_history()
            self.assertIn("Northern Plains of Karana", hist[0])
            self.assertLessEqual(sum(len(x) for x in hist), logwatch.HISTORY_CAP + 200)
            e = self._eng(d)
            e.prime(hist)
            self.assertEqual(e.zone, "The Northern Plains of Karana")
            self.assertEqual(e.loc, (100.0, -200.0, 5.0))
            self.assertEqual(e.level, 25)
            self.assertEqual(list(e.events)[0], ("zone", "The Northern Plains of Karana"))
            self.assertEqual(e.dps.fights(), [])
            self.assertEqual(w.take_history(), [])

    def test_zone_line_straddling_chunk_boundary(self):
        from eqtool_native import logwatch
        ts = "[Tue Sep 29 20:00:00 2026] "
        zone = (ts + "You have entered Befallen.\n").encode()
        tail = b"x" * (logwatch.CHUNK - 20) + b"\n"   # puts the zone line across the boundary
        with tempfile.TemporaryDirectory() as d:
            self._log(d, b"junk\n" * 1000 + zone + tail)
            w = LogWatcher(d)
            w.poll(0)
            e = self._eng(d)
            e.prime(w.take_history())
            self.assertEqual(e.zone, "Befallen")

    def test_no_zone_in_log(self):
        with tempfile.TemporaryDirectory() as d:
            self._log(d, b"[Tue Sep 29 20:00:00 2026] Hello\n")
            w = LogWatcher(d)
            w.poll(0)
            e = self._eng(d)
            e.prime(w.take_history())
            self.assertIsNone(e.zone)
            self.assertFalse(e.events)

    def test_who_footer_sets_zone(self):
        with tempfile.TemporaryDirectory() as d:
            e = self._eng(d)
            e.handle(1.0, "There are 3 players in Lower Guk.")
            self.assertEqual(e.zone, "Lower Guk")
            e.events.clear()
            e.handle(2.0, "There are 3 players in Lower Guk.")
            self.assertFalse(e.events)  # same zone, no churn
            e.handle(3.0, "There are 900 players in EverQuest.")
            self.assertEqual(e.zone, "Lower Guk")


class MapTests(unittest.TestCase):
    def test_fuzzy_and_subfolders(self):
        self.assertEqual(maps.short_name("The City of Kael Drakkal"), "kael")
        self.assertEqual(maps.short_name("The Nothern Plains of Karana"), "northkarana")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Maps", "brewall"))
            open(os.path.join(d, "Maps", "brewall", "befallen.txt"), "w").write(
                "L 0, 0, 0, 1, 1, 0, 0, 0, 0\n")
            dirs = maps.find_map_dirs(d)
            self.assertEqual([os.path.basename(x) for x in dirs[:2]], ["Maps", "brewall"])
            self.assertIn("befallen", maps.available_maps(dirs))
            self.assertIsNotNone(maps.load_map(dirs, maps.short_name("Befallen")))

    def test_zone_lookup_and_load(self):
        self.assertEqual(maps.short_name("The Northern Plains of Karana"), "northkarana")
        self.assertEqual(maps.short_name("Ak'Anon"), "akanon")
        self.assertEqual(maps.short_name("Weird Zone", aliases={"weird zone": "wz"}), "wz")
        self.assertEqual(maps.short_name("mystery", available=["mystery"]), "mystery")
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Qeynos.txt"), "w").write(
                "L 1.0, 2.0, 0, 3.0, 4.0, 0, 0, 0, 0\nL bad\nP 5, 6, 0, 255, 0, 0, 2, Guard_Hall\n")
            open(os.path.join(d, "qeynos_1.txt"), "w").write("L -10, -10, 0, 10, 10, 0, 0, 0, 0\n")
            md = maps.load_map(d, "qeynos")
            self.assertEqual(len(md.lines), 2)
            self.assertEqual(md.points[0][3], "Guard Hall")
            self.assertEqual(md.bounds, (-10, -10, 10, 10))
            self.assertEqual(maps.available_maps(d), ["qeynos"])


WIKI_SAMPLE = """{{Namedmobpage
| imagefilename = fippy.png
| emu_id = 2051
| zone = [[Qeynos Hills]]
| race = [[Gnoll]]
| class = [[Warrior]]
| level = 5
| respawn_time = 6 minutes 40 seconds
| known_loot = * [[Rusty Short Sword]] ''(Common)''
* [[Fippy's Tooth|Tooth]] ''(Rare)''
| special = {{Tooltip|Summons|text}}
| description = A <b>very</b> persistent gnoll.<br>He hates Qeynos.
}}
Some other page text {{Stub}}"""


class MobInfoTests(unittest.TestCase):
    def test_parse(self):
        kv = mobinfo.parse_mob_template(WIKI_SAMPLE)
        info = mobinfo.build_info("Fippy Darkpaw", "Fippy_Darkpaw", kv)
        d = dict(info.fields)
        self.assertEqual(d["Zone"], "Qeynos Hills")
        self.assertEqual(d["Level"], "5")
        self.assertIn("\u2022 Tooth (Rare)", d["Known loot"])
        self.assertEqual(d["Special"], "text")
        self.assertEqual(d["Description"], "A very persistent gnoll.\nHe hates Qeynos.")
        self.assertNotIn("Emu id", d)
        self.assertEqual(info.respawn_seconds, 400)

    def test_titles_and_durations(self):
        self.assertEqual(mobinfo.candidate_titles("a gnoll pup")[:2], ["A_gnoll_pup", "A_Gnoll_Pup"])
        self.assertIn("Gnoll_pup", mobinfo.candidate_titles("a gnoll pup"))
        self.assertEqual(mobinfo.parse_duration("22 hours"), 79200)
        self.assertEqual(mobinfo.parse_duration("6:40"), 400)
        self.assertEqual(mobinfo.parse_duration("1h 30m"), 5400)
        self.assertEqual(mobinfo.parse_duration("unknown"), 0)

    def test_service_cache(self):
        calls = []

        def fake(name):
            calls.append(name)
            return mobinfo.build_info(name, "X", {"level": "7"})
        with tempfile.TemporaryDirectory() as d:
            svc = mobinfo.MobInfoService(os.path.join(d, "c.json"), fetcher=fake)
            svc.request("a rat")
            import time
            for _ in range(50):
                got = svc.poll()
                if got:
                    break
                time.sleep(0.02)
            self.assertEqual(dict(got[0].fields)["Level"], "7")
            svc2 = mobinfo.MobInfoService(os.path.join(d, "c.json"), fetcher=fake)
            svc2.request("A Rat")
            self.assertEqual(dict(svc2.poll()[0].fields)["Level"], "7")
            self.assertEqual(calls, ["a rat"])


class SettingsTests(unittest.TestCase):
    def test_corrupt_and_bad_types(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            open(p, "w").write("{not json")
            s = Settings(p)
            self.assertEqual(s["font_size"], 10)
            open(p, "w").write('{"font_size": "big", "opacity": 1, "eq_dir": "/x"}')
            s = Settings(p)
            self.assertEqual((s["font_size"], s["opacity"], s["eq_dir"]), (10, 1, "/x"))
            s.save()
            self.assertEqual(Settings(p)["eq_dir"], "/x")


if __name__ == "__main__":
    unittest.main()


class BundledMapTests(unittest.TestCase):
    def test_every_zone_has_a_bundled_map(self):
        dirs = maps.find_map_dirs("")
        self.assertIn(maps.BUNDLED_DIR, dirs)
        avail = set(maps.available_maps(dirs))
        self.assertGreaterEqual(len(avail), 120)
        for long_name, short in maps.ZONES.items():
            self.assertIn(short, avail, long_name)

    def test_bundled_maps_load(self):
        for short in ("northkarana", "qeynos2", "gukbottom", "kael", "sebilis"):
            md = maps.load_map(maps.find_map_dirs(""), short)
            self.assertTrue(md and md.lines, short)
            x0, y0, x1, y1 = md.bounds
            self.assertLess(x0, x1)

    def test_eq_maps_override_bundled(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "maps"))
            open(os.path.join(d, "maps", "northkarana.txt"), "w").write("L 0,0,0,1,1,0,0,0,0\n")
            md = maps.load_map(maps.find_map_dirs(d), "northkarana")
            self.assertEqual(len(md.lines), 1)


class ZoneSearchTests(unittest.TestCase):
    def setUp(self):
        self.av = maps.available_maps(maps.find_map_dirs(""))

    def first(self, q):
        r = maps.search_zones(q, self.av)
        return r[0] if r else None

    def test_names_nicknames_typos(self):
        self.assertEqual(self.first("nk"), "northkarana")
        self.assertEqual(self.first("lguk"), "gukbottom")
        self.assertEqual(self.first("old guk"), "gukbottom")
        self.assertEqual(self.first("pof"), "fearplane")
        self.assertEqual(self.first("feerot"), "feerrott")       # typo
        self.assertEqual(self.first("karana north"), "northkarana")  # word order
        self.assertEqual(self.first("qey2hh1"), "qey2hh1")      # file name
        self.assertEqual(maps.search_zones("zzzzz", self.av), [])
        self.assertEqual(maps.search_zones("  ", self.av), [])

    def test_partial_lists_all_matches(self):
        self.assertEqual(set(maps.search_zones("freeport", self.av)),
                         {"freportn", "freportw", "freporte"})
        self.assertLessEqual(len(maps.search_zones("a", self.av, limit=10)), 10)

    def test_dropdown_labels(self):
        labels = dict(maps.zone_choices(["northkarana", "customzone"]))
        self.assertEqual(labels["Northern Plains of Karana (northkarana)"], "northkarana")
        self.assertEqual(labels["customzone"], "customzone")


class P99DurationTests(unittest.TestCase):
    """Formula table must match EQTool's P99 implementation exactly."""

    def test_formula_table_matches_eqtool(self):
        L = 60
        cases = {  # formula: (listed duration, expected ticks)
            1: (100, 30), 2: (100, 36), 3: (5000, 1800), 4: (0, 50), 5: (0, 3),
            6: (100, 30), 7: (100, 60), 8: (100, 70), 9: (500, 130), 10: (650, 190),
            11: (650, 650), 12: (40, 40), 13: (40, 40), 14: (40, 40), 15: (40, 40),
            50: (0, 72000), 3600: (0, 3600), 99: (12, 12),
        }
        for formula, (dur, want) in cases.items():
            self.assertEqual(duration_ticks(formula, dur, L), want, f"formula {formula}")

    def test_bedlam_seven_minutes(self):
        # P99 wiki: Bedlam lasts 6.8 min @L58, 7.0 min @L60.
        self.assertEqual(duration_ticks(8, 70, 58) * 6, 408)
        self.assertEqual(duration_ticks(8, 70, 60) * 6, 420)


class P99SpellFileTests(unittest.TestCase):
    def _db(self, *lines):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "spells_us.txt")
        open(p, "w", encoding="latin-1").write("\n".join(lines) + "\n")
        return SpellDB().load(p)

    def test_missing_target_type_is_not_skipped(self):
        db = self._db(spell_line(5, "Odd", "You feel odd.", "", "", dur=5, target=""))
        self.assertIn("You feel odd.", db.on_you)
        db = self._db(spell_line(6, "Corpse Thing", "You feel dead.", "", "", dur=5, target=15))
        self.assertNotIn("You feel dead.", db.on_you)

    def test_fixes_skips_and_caster_level(self):
        tele = spell_line(1, "Translocate: North", "You feel yourself pulled.", "", "", dur=5)
        gm = spell_line(2, "GM Test Buff", "You feel godly.", "", "", dur=5)
        pac = spell_line(3, "Pacify", "You feel pacified.", "", "", formula=3, dur=10, good=0)
        hi = spell_line(4, "Late Spell", "You feel late.", "", "", formula=7, dur=100, class_lvl=50)
        db = self._db(tele, gm, pac, hi)
        self.assertNotIn("You feel yourself pulled.", db.on_you)
        self.assertNotIn("You feel godly.", db.on_you)
        self.assertEqual(db.by_name["pacify"][0].duration_seconds(60), 35 * 6)
        # A level-20 player can't cast a level-50 spell; it was cast at >= 50.
        self.assertEqual(db.by_name["late spell"][0].duration_seconds(20), 50 * 6)

    def test_user_correction_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(os.path.join(d, "s.json"))
            db = self._db(spell_line(9, "Bedlam", "Your eyes gleam with bedlam.",
                                     "'s eyes gleam with bedlam.", "Your bedlam fades.",
                                     formula=11, dur=650))
            e = Engine(s, db)
            e.handle(1.0, "Your eyes gleam with bedlam.")
            t = list(e.timers.timers.values())[0]
            self.assertEqual(t.end - t.start, 3900)  # what the file says
            self.assertEqual(t.note, "Bedlam")
            s["spell_durations"] = {"bedlam": 420}
            e.handle(2.0, "Bob's eyes gleam with bedlam.")
            bob = [x for x in e.timers.timers.values() if x.target == "Bob"][0]
            self.assertEqual(bob.end - bob.start, 420)

    def test_level_lines_are_strict(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(os.path.join(d, "s.json"))
            e = Engine(s, SpellDB())
            e.handle(1.0, "Bob tells the group, 'You are now level 640 lol'")
            self.assertEqual(e.detected_level, 0)
            e.handle(1.0, "You have lost a level! You are now level 41.")
            self.assertEqual(e.detected_level, 41)


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        root = spell_line(230, "Root", "Your feet adhere to the ground.", " adheres to the ground.",
                          "Your feet come free.", formula=1, dur=5, good=0)
        charm = spell_line(300, "Allure", "You feel charmed.", " is charmed.", "", formula=3,
                           dur=20, good=0)
        f = charm.split("^"); f[86] = "22"; charm = "^".join(f)       # SPA 22 = charm
        oddroot = spell_line(301, "Vine Grasp", "Vines hold you.", " is held by vines.", "",
                             formula=3, dur=10, good=0)
        f = oddroot.split("^"); f[87] = "99"; oddroot = "^".join(f)   # SPA 99 = root
        wolf = spell_line(278, "Spirit of Wolf", "You feel the spirit of wolf enter you.",
                          " feels the spirit of wolf enter them.", "The spirit of wolf leaves you.",
                          formula=3, dur=1)
        open(os.path.join(self.d, "sp.txt"), "w").write("\n".join([root, charm, oddroot, wolf]))
        self.e = Engine(Settings(os.path.join(self.d, "s.json")),
                        SpellDB().load(os.path.join(self.d, "sp.txt")))

    def alerts(self):
        out = [d for k, d in self.e.events if k == "alert"]
        self.e.events.clear()
        return out

    def test_charm_break(self):
        self.e.handle(1.0, "You begin casting Allure.")
        self.e.handle(4.0, "a gnoll is charmed.")
        self.assertEqual(len(self.e.timers.timers), 1)
        self.alerts()
        self.e.handle(20.0, "Your charm spell has worn off.")
        a = self.alerts()
        self.assertEqual([(x.kind, x.text, x.say) for x in a], [("charm", "CHARM BREAK", "Charm break")])
        self.assertEqual(len(self.e.timers.timers), 0)

    def test_root_break_by_name_and_by_effect(self):
        self.e.handle(1.0, "a gnoll adheres to the ground.")
        self.e.handle(2.0, "a bat adheres to the ground.")
        self.e.handle(5.0, "Your Root spell has worn off.")
        a = self.alerts()
        self.assertEqual([(x.kind, x.say) for x in a], [("root", "Root break")])
        self.assertEqual(len(self.e.timers.timers), 1)  # only one of the two roots ended
        self.e.handle(6.0, "Your Vine Grasp spell has worn off.")
        self.assertEqual(self.alerts()[0].kind, "root")
        self.e.handle(7.0, "Your Paralyzing Earth spell has worn off.")  # not in file: name list
        self.assertEqual(self.alerts()[0].kind, "root")

    def test_other_worn_off(self):
        self.e.handle(1.0, "Your Tashanian spell has worn off.")
        a = self.alerts()
        self.assertEqual((a[0].kind, a[0].text), ("worn", "Tashanian wore off"))

    def test_timer_end_alerts_once_and_root_timers_stay_quiet(self):
        self.e.handle(0.0, "You feel the spirit of wolf enter you.")     # 6 s
        self.e.handle(0.0, "a gnoll adheres to the ground.")            # root timer
        self.alerts()
        self.e.tick(3.0)
        self.assertEqual(self.alerts(), [])
        self.e.tick(40.0)
        a = self.alerts()
        self.assertEqual([(x.kind, x.text) for x in a], [("self_fade", "Spirit of Wolf faded")])
        self.e.tick(41.0)
        self.assertEqual(self.alerts(), [])

    def test_custom_and_spawn_timer_alerts(self):
        self.e.timers.add("Pull", "Custom", 5, "custom", 0.0)
        self.e.start_spawn_timer("Fippy Darkpaw", 10, 0.0)
        self.e.tick(11.0)
        got = {(x.kind, x.text, x.say) for x in self.alerts()}
        self.assertIn(("custom", "Pull done", "Pull done"), got)
        self.assertIn(("spawn", "Respawn: Fippy Darkpaw", "Fippy Darkpaw respawning"), got)


class SpeechTests(unittest.TestCase):
    class FakeProc:
        def __init__(self, cmd, **kw):
            self.cmd, self.kw = cmd, kw
            self.stdin = self
            self.written = b""

        def poll(self):
            return 0

        def write(self, b):
            self.written += b

        def close(self):
            pass

    def make(self, have, custom="", engine="auto", result=None):
        import subprocess
        from eqtool_native.speech import Speaker
        self.calls, self.runs = [], []

        def popen(cmd, **kw):
            p = self.FakeProc(cmd, **kw)
            self.calls.append(p)
            return p

        def run(cmd, **kw):
            self.runs.append((cmd, kw))
            if isinstance(result, Exception):
                raise result
            return result or subprocess.CompletedProcess(cmd, 0, b"", b"")
        return Speaker(custom, engine, which=lambda n: f"/usr/bin/{n}" if n in have else None,
                       popen=popen, run=run)

    def test_engine_order_prefers_engines_that_fail_loudly(self):
        sp = self.make({"espeak-ng", "spd-say"})
        self.assertEqual(sp.name, "espeak-ng")                 # spd-say can be installed but silent
        self.assertTrue(sp.say("Charm break"))
        self.assertEqual(self.calls[-1].cmd, ["/usr/bin/espeak-ng", "-s", "165", "Charm break"])
        self.assertFalse(sp.say("Charm break"))                # repeat within 1.5 s is dropped
        self.assertEqual(self.make({"spd-say"}).command_for("Hi"),
                         (["/usr/bin/spd-say", "-w", "Hi"], None))   # waits until spoken
        self.assertEqual(self.make({"festival"}).command_for("Root break"),
                         (["/usr/bin/festival", "--tts"], "Root break"))
        self.assertEqual(self.make({"flite"}).command_for("Hi")[0], ["/usr/bin/flite", "-t", "Hi"])

    def test_you_can_choose_the_engine(self):
        sp = self.make({"espeak-ng", "spd-say", "flite"}, engine="spd-say")
        self.assertEqual(sp.name, "spd-say")
        self.assertEqual(sp.installed(), ["espeak-ng", "flite", "spd-say"])
        sp = self.make({"espeak-ng"}, engine="flite")          # chosen one missing: fall back
        self.assertEqual(sp.name, "espeak-ng")

    def test_none_and_custom(self):
        sp = self.make(set())
        self.assertFalse(sp.available)
        self.assertFalse(sp.say("Charm break"))
        sp = self.make(set(), custom="piper-say --voice amy {text}")
        sp.say("Charm break")
        self.assertEqual(self.calls[-1].cmd, ["piper-say", "--voice", "amy", "Charm break"])

    def test_text_is_cleaned_for_the_speech_program(self):
        from eqtool_native.speech import clean_text
        self.assertEqual(clean_text("Dead_Man  Floating"), "Dead Man Floating")
        self.assertEqual(clean_text("--help; rm -rf `x` $(y)"), "help rm -rf x (y)")
        self.assertEqual(clean_text("a" * 500), "a" * 140)
        self.assertEqual(clean_text(None), "")

    def test_speak_reports_what_happened(self):
        import subprocess
        ok = self.make({"espeak-ng"})
        self.assertEqual(ok.speak("Spirit of Wolf faded on Bob"), (True, "spoken with espeak-ng"))
        self.assertEqual(self.runs[-1][0][-1], "Spirit of Wolf faded on Bob")
        bad = self.make({"spd-say"}, result=subprocess.CompletedProcess(
            ["x"], 1, b"", b"Failed to connect to Speech Dispatcher"))
        good, detail = bad.speak("hi")
        self.assertFalse(good)
        self.assertIn("exit 1", detail)
        self.assertIn("Failed to connect to Speech Dispatcher", detail)       # the reason is not thrown away
        noisy = self.make({"spd-say"}, result=subprocess.CompletedProcess(["x"], 0, b"", b"no output module"))
        self.assertEqual(noisy.speak("hi")[0], True)
        self.assertIn("no output module", noisy.speak("hi")[1])               # exit 0 but it complained
        self.assertIn("did not finish", self.make({"espeak"}, result=subprocess.TimeoutExpired("x", 1)).speak("hi")[1])
        self.assertIn("was not found", self.make({"espeak"}, result=FileNotFoundError()).speak("hi")[1])
        none = self.make(set()).speak("hi")
        self.assertFalse(none[0])
        self.assertIn("espeak-ng", none[1])                                     # says what to install

    def test_install_hint_by_distro(self):
        from eqtool_native.speech import install_hint
        self.assertEqual(install_hint('ID=ubuntu\nID_LIKE=debian\n'), "sudo apt install espeak-ng")
        self.assertEqual(install_hint('ID=fedora\n'), "sudo dnf install espeak-ng")
        self.assertEqual(install_hint('ID=manjaro\nID_LIKE=arch\n'), "sudo pacman -S espeak-ng")
        self.assertEqual(install_hint('ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n'),
                         "sudo zypper install espeak-ng")
        self.assertEqual(install_hint('ID=steamos\nID_LIKE=arch\n'), "sudo pacman -S espeak-ng")
        self.assertIn("espeak-ng", install_hint('ID=plan9\n'))


class AnnouncerTests(unittest.TestCase):
    class Clips:
        def __init__(self, log):
            self.log = log

        def play(self, path, wait=False, timeout=10.0):
            self.log.append(("clip", path, wait))
            return True

    class Voice:
        def __init__(self, log, ok=True):
            self.log, self.ok = log, ok

        def speak(self, text, timeout=20.0):
            self.log.append(("say", text))
            return (self.ok, "spoken" if self.ok else "boom")

    def make(self, **kw):
        from eqtool_native.announcer import Announcer
        self.log = []
        return Announcer(self.Voice(self.log, kw.pop("ok", True)), self.Clips(self.log), start=False, **kw)

    def test_steps_play_in_order_and_clips_are_waited_for(self):
        a = self.make()
        a.run([("clip", "buff.wav"), ("say", "Spirit of Wolf faded on Bob")])
        self.assertEqual(self.log, [("clip", "buff.wav", True), ("say", "Spirit of Wolf faded on Bob")])
        self.assertEqual(a.last, "spoken")

    def test_the_same_clip_is_not_repeated_back_to_back(self):
        a = self.make()
        for who in ("Bob", "Kim", "Sue"):                       # three buffs fade together
            a.run([("clip", "buff.wav"), ("say", f"Clarity faded on {who}")])
        self.assertEqual([x for x in self.log if x[0] == "clip"], [("clip", "buff.wav", True)])
        self.assertEqual([x[1] for x in self.log if x[0] == "say"],
                         ["Clarity faded on Bob", "Clarity faded on Kim", "Clarity faded on Sue"])

    def test_too_many_waiting_drops_the_oldest(self):
        a = self.make(max_pending=3)
        for i in range(6):
            a.announce([("say", f"alert {i}")])
        got = []
        while True:
            steps = a._next(block=False)
            if steps is None:
                break
            got.append(steps[0][1])
        self.assertEqual(got, ["alert 3", "alert 4", "alert 5"])

    def test_a_failure_is_recorded_and_does_not_stop_the_rest(self):
        a = self.make(ok=False)
        a.run([("say", "one"), ("say", "two")])
        self.assertEqual(a.failures, 2)
        self.assertEqual(a.last, "boom")

    def test_urgent_announcements_jump_the_queue(self):
        a = self.make()
        a.announce([("say", "Clarity faded on Bob")])
        a.announce([("say", "Clarity faded on Kim")])
        a.announce([("say", "Spirit of Wolf fizzled")], urgent=True)
        a.announce([("say", "Charm break")], urgent=True)
        order = []
        while True:
            steps = a._next(block=False)
            if steps is None:
                break
            order.append(steps[0][1])
        self.assertEqual(order, ["Spirit of Wolf fizzled", "Charm break",       # urgent first, in order
                                 "Clarity faded on Bob", "Clarity faded on Kim"])

    def test_a_flood_never_drops_the_urgent_ones_for_normal_ones(self):
        a = self.make(max_pending=2)
        for i in range(5):
            a.announce([("say", f"fade {i}")])
        a.announce([("say", "fizzle")], urgent=True)
        self.assertEqual(a._next(block=False)[0][1], "fizzle")
        self.assertEqual([a._next(block=False)[0][1] for _ in range(2)], ["fade 3", "fade 4"])
        self.assertIsNone(a._next(block=False))

    def test_the_worker_thread_runs_announcements(self):
        import threading
        from eqtool_native.announcer import Announcer
        done = threading.Event()
        log = []

        class V:
            def speak(self, text, timeout=20.0):
                log.append(text)
                done.set()
                return True, "ok"
        a = Announcer(V(), self.Clips([]))
        a.announce([("say", "hello")])
        self.assertTrue(done.wait(3))
        a.close()
        self.assertEqual(log, ["hello"])


class SelfFadeTests(unittest.TestCase):
    """A buff falling off YOU is announced the moment the game says so."""

    def setUp(self):
        d = tempfile.mkdtemp()
        wolf = spell_line(278, "Spirit of Wolf", "You feel the spirit of wolf enter you.",
                          " is surrounded by a brief lupine aura.", "The spirit of wolf leaves you.",
                          formula=3, dur=10, class_lvl=10)           # 60 s
        root = spell_line(230, "Root", "Your feet adhere to the ground.", " adheres to the ground.",
                          "Your feet come free.", formula=1, dur=5, good=0)
        open(os.path.join(d, "sp.txt"), "w").write(wolf + "\n" + root + "\n")
        self.e = Engine(Settings(os.path.join(d, "s.json")), SpellDB().load(os.path.join(d, "sp.txt")))

    def alerts(self):
        out = [(a.kind, a.text, a.say) for k, a in self.e.events if k == "alert"]
        self.e.events.clear()
        return out

    def test_the_fade_message_announces_and_removes_the_timer(self):
        self.e.handle(0, "You feel the spirit of wolf enter you.")
        self.alerts()
        self.e.handle(40, "The spirit of wolf leaves you.")
        self.assertEqual(self.alerts(), [("self_fade", "Spirit of Wolf faded", "Spirit of Wolf faded")])
        self.assertEqual(len(self.e.timers.timers), 0)
        self.e.tick(100)
        self.assertEqual(self.alerts(), [])                      # and not a second time at expiry

    def test_the_timer_running_out_announces_when_no_message_comes(self):
        self.e.handle(0, "You feel the spirit of wolf enter you.")
        self.alerts()
        self.e.tick(30)
        self.assertEqual(self.alerts(), [])
        self.e.tick(70)
        self.assertEqual(self.alerts(), [("self_fade", "Spirit of Wolf faded", "Spirit of Wolf faded")])
        self.e.handle(71, "The spirit of wolf leaves you.")      # the message arrives late
        self.assertEqual(self.alerts(), [])                      # already announced

    def test_a_buff_we_never_saw_land_is_still_announced(self):
        self.e.handle(5, "The spirit of wolf leaves you.")      # cast before the app started
        self.assertEqual(self.alerts(), [("self_fade", "Spirit of Wolf faded", "Spirit of Wolf faded")])

    def test_a_debuff_fading_is_not_announced_as_a_buff(self):
        self.e.handle(0, "Your feet adhere to the ground.")
        self.alerts()
        self.e.handle(10, "Your feet come free.")
        self.assertEqual(self.alerts(), [])
        self.assertEqual(len(self.e.timers.timers), 0)

    def test_recast_buff_announces_once_per_fade(self):
        for t0 in (0, 200):
            self.e.handle(t0, "You feel the spirit of wolf enter you.")
            self.e.handle(t0 + 40, "The spirit of wolf leaves you.")
        self.assertEqual([a[0] for a in self.alerts()], ["self_fade", "self_fade"])


class MyBuffAlertTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        wolf = spell_line(278, "Spirit of Wolf", "You feel the spirit of wolf enter you.",
                          " feels the spirit of wolf enter them.", "The spirit of wolf leaves you.",
                          formula=3, dur=10)                     # 60 s
        c2 = spell_line(1693, "Clarity II", "A soft breeze slips through your mind.",
                        " looks very tranquil.", "Your mind returns to normal.", formula=3, dur=20)
        open(os.path.join(d, "sp.txt"), "w").write(wolf + "\n" + c2 + "\n")
        self.e = Engine(Settings(os.path.join(d, "s.json")), SpellDB().load(os.path.join(d, "sp.txt")))

    def alerts(self):
        out = [(a.kind, a.text, a.say) for k, a in self.e.events if k == "alert"]
        self.e.events.clear()
        return out

    def cast_on(self, target, t0=0.0, spell="Spirit of Wolf", msg=" feels the spirit of wolf enter them."):
        self.e.handle(t0, f"You begin casting {spell}.")
        self.e.handle(t0 + 3, target + msg)

    def test_timer_expiry_announces_with_player_name(self):
        self.cast_on("Bob")
        t = list(self.e.timers.timers.values())[0]
        self.assertTrue(t.mine)
        self.alerts()
        self.e.tick(30)
        self.assertEqual(self.alerts(), [])
        self.e.tick(70)
        self.assertEqual(self.alerts(), [("my_buff", "Spirit of Wolf faded on Bob",
                                          "Spirit of Wolf faded on Bob")])
        self.e.tick(71)
        self.assertEqual(self.alerts(), [])

    def test_game_message_announces_and_timer_stays_quiet(self):
        self.cast_on("Bob")
        self.alerts()
        self.e.handle(50, "Your Spirit of Wolf spell has worn off.")
        self.assertEqual(self.alerts(), [("my_buff", "Spirit of Wolf faded on Bob",
                                          "Spirit of Wolf faded on Bob")])
        self.e.tick(100)
        self.assertEqual(self.alerts(), [])  # no second announcement from the timer

    def test_timer_first_then_message_announces_once(self):
        self.cast_on("Bob")
        self.alerts()
        self.e.tick(70)
        self.assertEqual(len(self.alerts()), 1)
        self.e.handle(72, "Your Spirit of Wolf spell has worn off.")
        self.assertEqual(self.alerts(), [])

    def test_group_buff_picks_the_right_player(self):
        self.e.handle(0, "You begin casting Clarity II.")
        self.e.handle(3, "Bob looks very tranquil.")
        self.e.handle(3, "Kim looks very tranquil.")
        self.e.handle(3, "A soft breeze slips through your mind.")
        mine = [t for t in self.e.timers.timers.values() if t.mine]
        self.assertEqual(len(mine), 3)
        self.alerts()
        self.e.handle(100, "Your Clarity II spell has worn off.")
        self.e.handle(101, "Your Clarity II spell has worn off.")
        got = self.alerts()
        self.assertEqual(sorted(a[1] for a in got),
                         ["Clarity II faded on Bob", "Clarity II faded on Kim"])
        self.assertEqual([t.target for t in self.e.timers.timers.values()], ["You"])

    def test_someone_elses_buff_is_not_yours(self):
        self.e.handle(0, "Kim feels the spirit of wolf enter them.")   # you didn't cast
        t = list(self.e.timers.timers.values())[0]
        self.assertFalse(t.mine)
        self.e.tick(70)
        self.assertEqual([a[0] for a in self.alerts()], ["timer_other"])

    def test_message_without_a_timer_still_announces(self):
        self.e.handle(10, "Your Spirit of Wolf spell has worn off.")
        self.assertEqual(self.alerts(), [("my_buff", "Spirit of Wolf faded", "Spirit of Wolf faded")])


class ClipPlayerTests(unittest.TestCase):
    def make(self, have):
        from eqtool_native.speech import ClipPlayer
        self.calls = []

        class P:
            def __init__(s, cmd, **kw):
                self.calls.append(cmd)

            def poll(s):
                return 0
        return ClipPlayer(which=lambda n: f"/usr/bin/{n}" if n in have else None, popen=P)

    def test_bundled_clips_are_valid_audio(self):
        import wave
        from eqtool_native.speech import DEFAULT_CLIPS
        for kind, path in DEFAULT_CLIPS.items():
            with wave.open(path) as w:
                secs = w.getnframes() / w.getframerate()
            self.assertTrue(0.5 < secs < 2.0, kind)

    def test_player_choice_per_format(self):
        from eqtool_native.speech import DEFAULT_CLIPS
        wav = DEFAULT_CLIPS["charm"]
        with tempfile.NamedTemporaryFile(suffix=".mp3") as mp3:
            p = self.make({"pw-play", "mpv"})
            self.assertTrue(p.play(wav))
            self.assertEqual(self.calls[-1], ["/usr/bin/pw-play", wav])
            self.assertTrue(p.play(mp3.name))   # pw-play may not do MP3: use mpv
            self.assertEqual(self.calls[-1][0], "/usr/bin/mpv")
            p = self.make({"aplay", "mpg123"})
            p.play(mp3.name)
            self.assertEqual(self.calls[-1][:2], ["/usr/bin/mpg123", "-q"])
            p.play(wav)
            self.assertEqual(self.calls[-1][:2], ["/usr/bin/aplay", "-q"])
            p = self.make({"aplay"})
            self.assertFalse(p.can_play(mp3.name))   # nothing here can play MP3
            self.assertFalse(p.can_play("/no/such/file.wav"))

    def test_no_player_and_repeat_guard(self):
        from eqtool_native.speech import DEFAULT_CLIPS
        p = self.make(set())
        self.assertFalse(p.play(DEFAULT_CLIPS["root"]))
        p = self.make({"paplay"})
        self.assertTrue(p.play(DEFAULT_CLIPS["root"]))
        self.assertFalse(p.play(DEFAULT_CLIPS["root"]))  # same clip within 1 s


class ClockOffsetTests(unittest.TestCase):
    def line(self, epoch, msg="You feel quick."):
        return time.strftime("[%a %b %d %H:%M:%S %Y] ", time.localtime(epoch)) + msg

    def test_estimate(self):
        from eqtool_native.engine import estimate_clock_offset as est
        now = 1_800_000_000.0
        self.assertEqual(est([self.line(now - 2)], now), 0.0)              # normal 2 s delay
        self.assertEqual(est([self.line(now - 9)], now), 0.0)              # slow disk
        self.assertEqual(est([self.line(now + 3600 - 1)], now), -3600.0)   # log 1 h fast
        self.assertEqual(est([self.line(now - 3600 + 1)], now), 3600.0)    # log 1 h slow
        self.assertEqual(est([self.line(now + 5.5 * 3600)], now), -5.5 * 3600)  # half-hour zone
        self.assertEqual(est(["junk", "", self.line(now - 1), "more junk"], now), 0.0)
        self.assertIsNone(est(["junk"], now))
        self.assertIsNone(est([self.line(now - 40 * 3600)], now))          # not a time zone gap

    def test_timers_are_right_when_the_game_clock_is_an_hour_fast(self):
        from eqtool_native.engine import estimate_clock_offset as est
        d = tempfile.mkdtemp()
        sow = spell_line(278, "Spirit of Wolf", "You feel the spirit of wolf enter you.",
                         " is surrounded by a brief lupine aura.", "The spirit of wolf leaves you.",
                         cast=3000, formula=3, dur=360, class_lvl=10)
        open(os.path.join(d, "sp.txt"), "w").write(sow + "\n")
        s = Settings(os.path.join(d, "s.json"))
        s["level_override"] = 54
        e = Engine(s, SpellDB().load(os.path.join(d, "sp.txt")))
        now = time.time()
        lines = [self.line(now + 3600 - 3, "You begin casting Spirit of Wolf."),
                 self.line(now + 3600 - 0, "You feel the spirit of wolf enter you.")]
        off = est(lines, now)
        for ln in lines:
            e.feed_raw(ln, off)
        e.tick(now)
        t = list(e.timers.timers.values())[0]
        self.assertEqual(t.label, "Spirit of Wolf")
        self.assertAlmostEqual(t.end - now, 36 * 60, delta=3)       # 36 minutes, not 1:36
        # ...and the cast line is still recognised as "just cast" (pending not wiped by tick)
        self.assertTrue(t.mine)

    def test_without_correction_the_timer_would_be_an_hour_long(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "sp.txt"), "w").write(spell_line(
            278, "Spirit of Wolf", "You feel the spirit of wolf enter you.", "", "", formula=3,
            dur=360) + "\n")
        e = Engine(Settings(os.path.join(d, "s.json")), SpellDB().load(os.path.join(d, "sp.txt")))
        now = time.time()
        e.feed_raw(self.line(now + 3600, "You feel the spirit of wolf enter you."))
        self.assertGreater(list(e.timers.timers.values())[0].end - now, 90 * 60)


class SharedMessageTests(unittest.TestCase):
    """Spirit of Wolf and Pack Spirit print identical messages (P99 wiki)."""

    def make(self, pack_dur=360):
        d = tempfile.mkdtemp()
        sow = spell_line(278, "Spirit of Wolf", "You feel the spirit of wolf enter you.",
                         " is surrounded by a brief lupine aura.", "The spirit of wolf leaves you.",
                         cast=3000, formula=3, dur=360, class_lvl=10)
        pack = spell_line(3, "Pack Spirit", "You feel the spirit of wolf enter you.",
                          " is surrounded by a brief lupine aura.", "The spirit of wolf leaves you.",
                          cast=6500, formula=3, dur=pack_dur, class_lvl=39, target=41)
        open(os.path.join(d, "sp.txt"), "w").write(sow + "\n" + pack + "\n")
        s = Settings(os.path.join(d, "s.json"))
        s["level_override"] = 54
        return Engine(s, SpellDB().load(os.path.join(d, "sp.txt")))

    def labels(self, e):
        return [(t.label, round(t.end - t.start)) for t in e.timers.timers.values()]

    def test_self_cast_is_spirit_of_wolf(self):
        e = self.make()
        e.handle(100, "You begin casting Spirit of Wolf.")
        e.handle(103, "You feel the spirit of wolf enter you.")
        self.assertEqual(self.labels(e), [("Spirit of Wolf", 2160)])

    def test_no_cast_line_prefers_the_plain_spell_and_does_not_nag(self):
        e = self.make()                     # e.g. a clicky: no "You begin casting"
        e.handle(100, "You feel the spirit of wolf enter you.")
        self.assertEqual(self.labels(e), [("Spirit of Wolf", 2160)])     # not Pack Spirit, no (?)
        e.handle(100, "Bob is surrounded by a brief lupine aura.")
        by_target = {t.target: t.label for t in e.timers.timers.values()}
        self.assertEqual(by_target, {"You": "Spirit of Wolf", "Bob": "Spirit of Wolf"})

    def test_group_cast_picks_pack_spirit(self):
        e = self.make()
        e.handle(100, "You begin casting Pack Spirit.")
        e.handle(107, "You feel the spirit of wolf enter you.")
        self.assertEqual(self.labels(e), [("Pack Spirit", 2160)])

    def test_question_mark_only_when_the_timers_differ(self):
        e = self.make(pack_dur=720)         # pretend Pack Spirit lasted twice as long
        e.handle(100, "You feel the spirit of wolf enter you.")
        self.assertEqual(self.labels(e)[0][0], "Spirit of Wolf (?)")


class RespawnTableTests(unittest.TestCase):
    def setUp(self):
        from eqtool_native.respawn import RespawnTable
        self.t = RespawnTable()

    def test_shipped_data_is_complete(self):
        self.assertEqual(len(self.t), 121)
        for short in set(maps.ZONES.values()):          # every zone the app knows has a time
            self.assertTrue(self.t.has_zone(short), short)

    def test_lookup_order_is_eqtools(self):
        # zone-wide time, "name contains" entries, exact entries, unknown zone
        self.assertEqual(self.t.lookup("a gnoll", "butcher"), (900, "zone"))
        self.assertEqual(self.t.lookup("Guard Belg", "butcher"), (1440, "name"))
        self.assertEqual(self.t.lookup("guard zyburr", "butcher"), (1120, "name"))     # case-insensitive
        self.assertEqual(self.t.lookup("Atwin Keladryn", "butcher"), (400, "npc"))
        self.assertEqual(self.t.lookup("a gnoll", "no_such_zone"), (400, "default"))
        self.assertEqual(self.t.lookup("a gnoll", None), (400, "default"))
        self.assertEqual(self.t.lookup("a gnoll", "no_such_zone", default=500), (500, "default"))
        self.assertEqual(self.t.lookup("a gnoll", "befallen")[1], "zone")
        self.assertEqual(self.t.lookup("a gnoll", "blackburrow"), (1320, "zone"))      # not everywhere is 6:40

    def test_zone_with_no_respawn_uses_the_default(self):
        self.assertEqual(self.t.lookup("a fighter", "arena"), (400, "default"))

    def test_missing_file_is_harmless(self):
        from eqtool_native.respawn import RespawnTable
        t = RespawnTable("/nonexistent/file.json")
        self.assertEqual(len(t), 0)
        self.assertEqual(t.lookup("x", "butcher"), (400, "default"))


class KillTimerTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.s = Settings(os.path.join(d, "s.json"))
        self.e = Engine(self.s, SpellDB())
        self.e.handle(0.0, "You have entered Butcherblock Mountains.")
        self.e.events.clear()

    def spawns(self):
        return sorted((t.label, round(t.end - t.start)) for t in self.e.timers.timers.values()
                      if t.kind == "spawn")

    def test_defaults_are_on(self):
        self.assertTrue(self.s["auto_spawn_timers"])
        self.assertTrue(self.s["spawn_needs_experience"])
        self.assertFalse(self.s["alert_spawns"])        # a banner per kill would be a flood

    def test_timer_starts_on_the_experience_line(self):
        self.e.handle(100.0, "You have slain a gnoll!")
        self.assertEqual(self.spawns(), [])             # not yet: no experience
        self.e.handle(100.0, "You gain experience!!")
        self.assertEqual(self.spawns(), [("a gnoll", 900)])      # Butcherblock's zone time
        t = [x for x in self.e.timers.timers.values() if x.kind == "spawn"][0]
        self.assertEqual(t.target, "Butcherblock Mountains")
        self.assertEqual(t.start, 100.0)                # counted from the death, not from the xp line

    def test_kill_without_experience_gets_no_timer(self):
        self.e.handle(100.0, "You have slain a rat!")
        self.e.handle(120.0, "You gain experience!!")   # too late to belong to the rat
        self.assertEqual(self.spawns(), [])

    def test_party_kill_and_npc_specific_times(self):
        self.e.handle(100.0, "Guard Belg has been slain by Bob!")
        self.e.handle(101.0, "You gain party experience!!")
        self.assertEqual(self.spawns(), [("Guard Belg", 1440)])

    def test_every_kill_gets_its_own_timer(self):
        for i, mob in enumerate(("a gnoll", "a gnoll", "a bear")):
            self.e.handle(100.0 + i * 10, f"You have slain {mob}!")
            self.e.handle(100.0 + i * 10 + 1, "You gain experience!!")
        self.assertEqual(self.spawns(), [("a bear", 900), ("a gnoll", 900), ("a gnoll", 900)])

    def test_your_kill_wins_over_someone_elses_in_the_same_moment(self):
        self.e.handle(100.0, "You have slain a gnoll!")
        self.e.handle(100.0, "a bear has been slain by Bob!")
        self.e.handle(100.0, "You gain experience!!")
        self.assertEqual([n for n, _ in self.spawns()], ["a gnoll"])

    def test_your_own_rule_starts_at_once_and_only_once(self):
        self.s["spawn_rules"] = {"a gnoll": 1200}
        self.e.handle(100.0, "You have slain a gnoll!")
        self.assertEqual(self.spawns(), [("a gnoll", 1200)])        # no experience needed
        self.e.handle(100.0, "You gain experience!!")
        self.assertEqual(len(self.spawns()), 1)                      # and not twice

    def test_settings_change_the_trigger(self):
        self.s["spawn_needs_experience"] = False
        self.e.handle(100.0, "You have slain a gnoll!")
        self.assertEqual(self.spawns(), [("a gnoll", 900)])          # on the death itself
        self.s["auto_spawn_timers"] = False
        self.e.handle(200.0, "You have slain a bear!")
        self.e.handle(200.0, "You gain experience!!")
        self.assertEqual(len(self.spawns()), 1)                      # switched off

    def test_location_label_only_when_the_loc_is_recent(self):
        self.e.handle(100.0, "Your Location is 10.00, 20.00, 0.00")
        self.e.handle(110.0, "You have slain a gnoll!")
        self.e.handle(110.0, "You gain experience!!")
        self.e.handle(900.0, "You have slain a bear!")               # the /loc is now 13 minutes old
        self.e.handle(900.0, "You gain experience!!")
        self.assertEqual([n for n, _ in self.spawns()], ["a bear", "a gnoll @ 10, 20"])

    def test_unknown_zone_uses_the_default_time(self):
        self.e.handle(10.0, "You have entered Some Made Up Place.")
        self.e.handle(100.0, "You have slain a thing!")
        self.e.handle(100.0, "You gain experience!!")
        self.assertEqual(self.spawns(), [("a thing", 400)])

    def test_precedence_rule_npc_wiki_zone(self):
        self.e.respawn_lookup = lambda mob: 777                      # a figure from the wiki
        self.assertEqual(self.e.spawn_seconds("a gnoll"), (777.0, "wiki"))        # beats the zone time
        self.assertEqual(self.e.spawn_seconds("Guard Belg"), (1440.0, "name"))   # NPC-specific beats the wiki
        self.s["spawn_rules"] = {"guard belg": 60}
        self.assertEqual(self.e.spawn_seconds("Guard Belg"), (60.0, "your rule"))

    def test_experience_line_alone_is_harmless(self):
        self.e.handle(5.0, "You gain experience!!")
        self.e.handle(6.0, "You gain party experience!!")
        self.assertEqual(self.e.errors, 0)
        self.assertEqual(self.spawns(), [])


class MapLevelTests(unittest.TestCase):
    def test_eqtools_fade_rule(self):
        h = 10.0
        self.assertEqual(maps.level_opacity(0, h), 1.0)
        self.assertEqual(maps.level_opacity(9.9, h), 1.0)            # inside one level height
        self.assertEqual(maps.level_opacity(10, h), 1.0)             # (3h-dz)/2h + 0.1 = 1.1, capped
        self.assertAlmostEqual(maps.level_opacity(20, h), 0.6)       # halfway through the fade
        self.assertAlmostEqual(maps.level_opacity(30, h), 0.1)       # three level heights away
        self.assertEqual(maps.level_opacity(31, h), 0.1)             # then the floor value
        self.assertEqual(maps.level_opacity(500, h), 0.1)
        self.assertEqual(maps.level_opacity(7, 0), 1.0)              # a zero height can't hide anything

    def test_a_line_is_as_near_as_its_nearer_end(self):
        self.assertEqual(maps.line_dz(0, -30, -2), 2)                # a ramp counts from the top floor
        self.assertEqual(maps.line_dz(0, -30, -31), 1)               # ...and from the middle one
        self.assertEqual(maps.line_dz(-60, -60, 0), 60)

    def test_zone_level_table(self):
        self.assertEqual(maps.level_info("unrest"), (False, 4.0))
        self.assertEqual(maps.level_info("gukbottom"), (False, 10.0))
        self.assertEqual(maps.level_info("befallen")[0], True)       # EQTool shows these whole
        self.assertEqual(maps.level_info("GukBottom"), (False, 10.0))
        self.assertEqual(maps.level_info("no_such_zone")[0], True)   # unknown: don't hide anything
        for zone in set(maps.ZONES.values()):
            self.assertIn(zone, maps._levels()["zones"], zone)

    def test_heights_are_kept_when_a_map_is_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "x.txt"), "w").write(
                "L 0,0,5, 10,0,-7, 1,2,3\nP 4,4,-12, 9,9,9, 2, A_Label\n")
            md = maps.load_map(d, "x")
            self.assertEqual(md.lines[0][5:], (5.0, -7.0))
            self.assertEqual((md.points[0][3], md.points[0][4]), ("A Label", -12.0))
            self.assertEqual(md.bounds, (0.0, 0.0, 10.0, 4.0))       # bounds still work with heights

    def test_floors_are_found_from_flat_runs_not_ramps(self):
        md = maps.MapData("x")
        for z in (0, -30, -60):                                      # three floors, long flat lines
            md.lines.append((0, 0, 100, 0, (1, 1, 1), z, z))
        for i in range(30):                                          # a ramp: many different heights
            md.lines.append((50, i, 50, i + 1, (1, 1, 1), -i, -i - 1))
        self.assertEqual(maps.level_centers(md, 10), [-60.0, -30.0, 0.0])
        near = maps.MapData("y")                                     # two lines 4 apart: one floor
        near.lines += [(0, 0, 100, 0, (1, 1, 1), 0, 0), (0, 1, 100, 1, (1, 1, 1), 4, 4)]
        self.assertEqual(len(maps.level_centers(near, 10)), 1)
        self.assertEqual(maps.level_centers(maps.MapData("z"), 10), [])


class CastFailureAndWatchTests(unittest.TestCase):
    """Fizzles, interrupts and spells you asked to hear about."""

    def setUp(self):
        d = tempfile.mkdtemp()
        wolf = spell_line(278, "Spirit of Wolf", "You feel the spirit of wolf enter you.",
                          " is surrounded by a brief lupine aura.", "The spirit of wolf leaves you.",
                          cast=3000, formula=3, dur=10, class_lvl=10)
        # The shaman epic's DoT, as the P99 wiki describes it: 15 ticks, detrimental.
        curse = spell_line(1932, "Curse of the Spirits", "Your body is consumed by the raging spirits of the land.",
                           " is consumed by the raging spirits of the land.", "The spirits have subsided.",
                           cast=9000, formula=3, dur=15, good=0, class_lvl=50)
        root = spell_line(230, "Root", "Your feet adhere to the ground.", " adheres to the ground.",
                          "Your feet come free.", formula=1, dur=5, good=0)
        open(os.path.join(d, "sp.txt"), "w").write("\n".join([wolf, curse, root]) + "\n")
        self.s = Settings(os.path.join(d, "s.json"))
        self.e = Engine(self.s, SpellDB().load(os.path.join(d, "sp.txt")))

    def alerts(self):
        out = [(a.kind, a.text, a.say) for k, a in self.e.events if k == "alert"]
        self.e.events.clear()
        return out

    # -- fizzles and interrupts
    def test_fizzle_names_the_spell(self):
        self.e.handle(0, "You begin casting Spirit of Wolf.")
        self.alerts()
        self.e.handle(2, "Your spell fizzles!")
        self.assertEqual(self.alerts(), [("fizzle", "FIZZLE: Spirit of Wolf", "Spirit of Wolf fizzled")])
        self.assertIsNone(self.e.pending)

    def test_interrupt_names_the_spell(self):
        self.e.handle(0, "You begin casting Curse of the Spirits.")
        self.alerts()
        self.e.handle(4, "Your spell is interrupted.")
        self.assertEqual(self.alerts(), [("interrupt", "INTERRUPTED: Curse of the Spirits",
                                          "Curse of the Spirits interrupted")])
        self.e.handle(10, "You begin casting Spirit of Wolf.")
        self.e.handle(11, "Your casting has been interrupted!")
        self.assertEqual(self.alerts()[-1][0], "interrupt")

    def test_dot_on_a_mob_gets_the_extra_tick(self):
        from eqtool_native.spells import Spell
        dot = Spell(1, "Test Dot", "", " is burned.", "", 2000, 0, 4, 10, False, 10)
        buff = Spell(2, "Test Buff", "", " is strong.", "", 2000, 0, 4, 10, True, 10)
        self.assertTrue(self.e._extra_tick(dot, "a gnoll"))
        self.assertFalse(self.e._extra_tick(dot, "Bob"))        # a player
        self.assertFalse(self.e._extra_tick(dot, "You"))
        self.assertFalse(self.e._extra_tick(buff, "a gnoll"))   # buffs keep the listed time
        self.e._add_spell_timer(dot, "a gnoll", 100.0, False)
        t = next(iter(self.e.timers.timers.values()))
        self.assertEqual(t.end - t.start, 10 * 6 + 6)

    def test_grumpy_tree_trap(self):
        self.e.handle(500, "a grumpy tree throws a bunch of pansies at you")
        self.assertEqual(self.alerts(), [("trap", "PANSY TRAP!", "Pansy trap")])

    def test_pansy_trap_alert(self):
        self.e.handle(1000, "A pretty ugly pansy hits YOU for 137 points of damage.")
        self.assertEqual(self.alerts()[0], ("trap", "PANSY TRAP!", "Pansy trap"))
        self.e.handle(1010, "A dandy pansy hits YOU for 99 points of damage.")
        self.assertEqual(self.alerts(), [])                 # one alert per burst
        self.e.handle(1100, "You have triggered a trap!")
        self.assertEqual(self.alerts()[0][0], "trap")
        self.e.handle(1200, "Bob says, 'the trap on the left is nasty'")
        self.assertEqual(self.alerts(), [])                 # chat does not count
        self.s["trap_phrases"] = ["the ground shakes"]
        self.e.handle(1300, "The ground shakes beneath you.")
        self.assertEqual(self.alerts()[0][0], "trap")

    def test_group_invite_is_announced(self):
        self.e.handle(0, "Soren invites you to join a group.")
        self.assertEqual(self.alerts(), [("invite", "GROUP INVITE: Soren", "Soren invites you to join a group")])
        self.e.handle(1, "Bob invites you to join a raid.")
        self.assertEqual(self.alerts()[0][0], "invite")
        self.e.handle(2, "You invite Bob to join your group.")
        self.e.handle(3, "Soren tells you, 'he invites you to join a group.'")
        self.assertEqual(self.alerts(), [])

    def test_without_a_cast_in_progress_it_still_speaks(self):
        self.e.handle(0, "Your spell fizzles!")
        self.e.handle(1, "Your spell is interrupted.")
        self.assertEqual(self.alerts(), [("fizzle", "FIZZLE", "Fizzle"),
                                         ("interrupt", "INTERRUPTED", "Interrupted")])

    def test_other_cast_problems_stay_quiet(self):
        self.s["triggers"] = []          # the built-in triggers do speak up about these
        for msg in ("Insufficient Mana to cast this spell!", "You cannot see your target.",
                    "Your spell did not take hold.", "Your target resisted the Root spell."):
            self.e.handle(0, "You begin casting Root.")
            self.e.handle(1, msg)
        self.assertEqual(self.alerts(), [])

    def test_every_fizzle_is_announced(self):
        for i in range(3):
            self.e.handle(i * 10, "You begin casting Spirit of Wolf.")
            self.e.handle(i * 10 + 1, "Your spell fizzles!")
        self.assertEqual([a[0] for a in self.alerts() if a[0] == "fizzle"], ["fizzle"] * 3)

    # -- spells you watch
    def land_curse(self, target="a gnoll", t=0.0):
        self.e.handle(t, "You begin casting Curse of the Spirits.")
        self.e.handle(t + 9, f"{target} is consumed by the raging spirits of the land.")

    def test_curse_of_the_spirits_is_watched_by_default(self):
        self.assertEqual(self.s["watch_spells"], ["Curse of the Spirits"])
        self.assertTrue(self.e._watched("Curse of the Spirits"))
        self.assertTrue(self.e._watched("curse of spirits"))         # the way you said it
        self.assertFalse(self.e._watched("Spirit of Wolf"))

    def test_it_wearing_off_is_announced_with_the_mob(self):
        self.land_curse()
        self.assertEqual([t.kind for t in self.e.timers.timers.values()], ["debuff"])
        self.alerts()
        self.e.handle(95, "Your Curse of the Spirits spell has worn off.")
        self.assertEqual(self.alerts(), [("watch", "Curse of the Spirits wore off on a gnoll",
                                          "Curse of the Spirits wore off on a gnoll")])
        self.assertEqual(len(self.e.timers.timers), 0)

    def test_the_list_may_spell_it_your_way_and_the_spells_own_fade_text_counts(self):
        self.s["watch_spells"] = ["curse of spirits"]                           # typed without "the"
        self.e.handle(10, "Your Curse of the Spirits spell has worn off.")      # no timer known
        self.assertEqual(self.alerts(), [("watch", "Curse of the Spirits wore off",
                                          "Curse of the Spirits wore off")])
        self.e.handle(100, "The spirits have subsided.")                        # the fade text
        self.assertEqual([a[0] for a in self.alerts()], ["watch"])

    def test_timer_running_out_announces_once(self):
        self.land_curse()
        self.alerts()
        self.e.tick(60)
        self.assertEqual(self.alerts(), [])
        self.e.tick(120)
        self.assertEqual([a[0] for a in self.alerts()], ["watch"])
        self.e.handle(121, "Your Curse of the Spirits spell has worn off.")     # message arrives late
        self.assertEqual(self.alerts(), [])                                     # already announced

    def test_the_message_first_then_the_timer_stays_quiet(self):
        self.land_curse()
        self.alerts()
        self.e.handle(92, "Your Curse of the Spirits spell has worn off.")
        self.assertEqual(len(self.alerts()), 1)
        self.e.tick(200)
        self.assertEqual(self.alerts(), [])

    def test_a_dead_mob_needs_no_announcement(self):
        self.land_curse()
        self.alerts()
        self.e.handle(40, "You have slain a gnoll!")
        self.e.tick(200)
        self.assertEqual([a for a in self.alerts() if a[0] == "watch"], [])

    def test_a_spell_not_on_the_list_is_not_announced(self):
        self.e.handle(0, "You begin casting Spirit of Wolf.")
        self.e.handle(3, "Bob is surrounded by a brief lupine aura.")
        self.alerts()
        self.s["watch_spells"] = []
        self.e.handle(50, "Your Curse of the Spirits spell has worn off.")
        self.assertEqual([a[0] for a in self.alerts()], ["worn"])               # the old, quiet behaviour

    def test_you_can_watch_something_else(self):
        self.s["watch_spells"] = ["Curse of the Spirits", "Spirit of Wolf"]
        self.e.handle(0, "You begin casting Spirit of Wolf.")
        self.e.handle(3, "Bob is surrounded by a brief lupine aura.")
        self.alerts()
        self.e.handle(60, "Your Spirit of Wolf spell has worn off.")
        self.assertEqual(self.alerts(), [("watch", "Spirit of Wolf wore off on Bob",
                                          "Spirit of Wolf wore off on Bob")])

    def test_spell_name_matching(self):
        from eqtool_native.spells import norm_spell
        self.assertEqual(norm_spell("Curse of the Spirits"), norm_spell("curse of  SPIRITS"))
        self.assertEqual(norm_spell("Bind Sight"), "bind sight")
        self.assertNotEqual(norm_spell("Spirit of Wolf"), norm_spell("Spirit of the Wolf Pack"))
        self.assertEqual(norm_spell(None), "")


class BuffFilterTests(unittest.TestCase):
    def setUp(self):
        from eqtool_native.trackers import TimerManager
        tm = TimerManager()
        tm.add("SoW", "You", 60, "buff", 0, key="a", mine=True)              # I cast it on myself
        tm.add("Clarity", "You", 60, "buff", 0, key="b", mine=False)         # a druid put it on me
        tm.add("Haste", "Bob", 60, "buff", 0, key="c", mine=True)            # I cast it on Bob
        tm.add("Regen", "Kim", 60, "buff", 0, key="d", mine=False)           # someone else buffed Kim
        tm.add("Root", "a gnoll", 30, "debuff", 0, key="e", mine=True)
        tm.add("Pull", "Custom", 60, "custom", 0, key="f")
        tm.add("Gate", "Recast", 60, "recast", 0, key="g")
        self.t = tm.timers

    def names(self, mode):
        from eqtool_native.trackers import buff_visible
        return sorted(t.label for t in self.t.values() if buff_visible(t, mode))

    def test_everything(self):
        self.assertEqual(len(self.names("all")), 7)
        self.assertEqual(len(self.names("anything else")), 7)        # an unknown value changes nothing

    def test_mine_and_me_hides_other_peoples_buffs_on_other_people(self):
        self.assertEqual(self.names("mine_and_me"),
                         ["Clarity", "Gate", "Haste", "Pull", "Root", "SoW"])     # not Kim's Regen

    def test_only_buffs_i_cast(self):
        self.assertEqual(self.names("mine"), ["Gate", "Haste", "Pull", "Root", "SoW"])   # not Clarity on me either

    def test_only_buffs_are_filtered(self):
        for mode in ("mine", "mine_and_me"):
            got = self.names(mode)
            for keep in ("Root", "Pull", "Gate"):                    # debuff, custom timer, recast
                self.assertIn(keep, got)


class SettingsMigrationTests(unittest.TestCase):
    def test_hover_hide_saved_by_the_old_version_is_reset_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            import json
            json.dump({"ghost_under_mouse": True, "font_size": 12}, open(p, "w"))   # an old file: no version
            s = Settings(p)
            self.assertIsNone(s["ghost_under_mouse"])                  # back to automatic
            self.assertEqual(s["font_size"], 12)                       # nothing else is touched
            s["ghost_under_mouse"] = True                              # a choice made now
            s.save()
            self.assertTrue(Settings(p)["ghost_under_mouse"])          # is kept next time
            self.assertEqual(Settings(p)["settings_version"], 2)

    def test_a_fresh_install_is_automatic(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(Settings(os.path.join(d, "none.json"))["ghost_under_mouse"])


class BrandTests(unittest.TestCase):
    def test_name_and_version(self):
        import eqtool_native as pkg
        self.assertEqual(pkg.APP_TITLE, "Everlast by Sarekrn")
        self.assertRegex(pkg.__version__, r"^\d+\.\d$")      # 0.1, 0.2, ...


class RaidAndTriggerTests(unittest.TestCase):
    """Custom triggers, Dragon Roar, enrage and Death Touch."""

    def setUp(self):
        d = tempfile.mkdtemp()
        roar = spell_line(1100, "Dragon Roar", "You are blasted by a mighty roar.",
                          " is blasted by a mighty roar.", "", cast=0, recast=36000,
                          formula=0, dur=0, good=0)
        breath = spell_line(1101, "Lava Breath", "You are scorched by lava.",
                            " is scorched by lava.", "", cast=0, recast=20000,
                            formula=0, dur=0, good=0)
        open(os.path.join(d, "sp.txt"), "w").write("\n".join([roar, breath]) + "\n")
        self.s = Settings(os.path.join(d, "s.json"))
        self.e = Engine(self.s, SpellDB().load(os.path.join(d, "sp.txt")))
        self.e.zone = "Nagafen's Lair"          # soldungb: Lord Nagafen casts Dragon Roar

    def events(self):
        out = [a for k, a in self.e.events if k == "alert"]
        self.e.events.clear()
        return out

    # -- custom triggers
    def test_all_of_eqtools_triggers_are_there(self):
        from eqtool_native.triggers import default_triggers
        d = default_triggers()
        self.assertEqual([t["id"] for t in d], list(range(100, 115)))
        self.assertEqual(sum(1 for t in d if t["enabled"]), 13)   # 100, 101 off: Everlast's own

    def test_trigger_shows_text_and_fills_placeholders(self):
        self.e.handle(10, "Bob backstabs a gnoll for 321 points of damage.")
        a = self.events()
        self.assertEqual((a[0].kind, a[0].text, a[0].speak), ("trigger", "Bob backstabs a gnoll for 321", False))
        self.e.handle(11, "You think you are heading North.")
        self.assertEqual(self.events()[0].text, "Direction = North")

    def test_trigger_that_speaks(self):
        self.s["triggers"] = [{"id": 1000, "enabled": True, "name": "oom", "search": r"^Insufficient Mana",
                               "text_on": False, "text": "", "audio_on": True, "audio": "out of mana"}]
        self.e.handle(10, "Insufficient Mana to cast this spell!")
        a = self.events()[0]
        self.assertEqual((a.show, a.speak, a.say), (False, True, "out of mana"))

    def test_bad_pattern_is_skipped_and_edits_apply_at_once(self):
        self.s["triggers"] = [{"id": 1, "enabled": True, "name": "bad", "search": "(",
                               "text_on": True, "text": "x", "audio_on": False, "audio": ""}]
        self.e.handle(10, "(")
        self.assertEqual(self.events(), [])

    def test_trigger_file_round_trip(self):
        from eqtool_native.triggers import default_triggers, format_file, parse_file
        got, bad = parse_file(format_file(default_triggers()))
        self.assertEqual((got, bad), (default_triggers(), 0))
        self.assertEqual(parse_file("garbage\n# c\n")[1], 1)

    # -- enrage
    def test_enrage_warns_then_says_it_is_over(self):
        self.e.handle(100, "a ghoul lord has become ENRAGED.")
        a = self.events()[0]
        self.assertEqual((a.kind, a.text, a.say), ("enrage", "a ghoul lord ENRAGED", "a ghoul lord is enraged"))
        self.e.tick(105)
        self.assertEqual(self.events(), [])
        self.e.tick(112.5)                                  # 12 s later
        a = self.events()[0]
        self.assertEqual((a.text, a.speak), ("a ghoul lord ENRAGE OFF", False))

    # -- Death Touch
    def test_death_touch_names_the_target_and_starts_a_timer(self):
        self.e.character = "Soren"
        self.e.handle(100, "Fright says, 'Luetin'")
        a = self.events()[0]
        self.assertEqual((a.kind, a.text, a.say), ("dt", "DEATH TOUCH: Luetin", "Death touch Luetin"))
        t = list(self.e.timers.timers.values())[0]
        self.assertEqual((t.label, t.end - t.start), ("--DT-- 'Luetin'", 45))
        self.e.handle(110, "Dread says, 'Soren'")
        self.assertEqual(self.events()[0].text, "DEATH TOUCH ON YOU!")

    def test_death_touch_ignores_other_talk(self):
        self.e.handle(100, "Fright says, 'You will pay for this, mortal'")
        self.e.handle(101, "Bob says, 'Luetin'")
        self.assertEqual(self.events(), [])

    # -- Dragon Roar
    def test_dragon_roar_countdown(self):
        self.e.handle(1000, "You are blasted by a mighty roar.")
        a = self.events()
        self.assertEqual((a[0].kind, a[0].text, a[0].say), ("roar", "DRAGON ROAR!", "Dragon Roar out"))
        t = self.e.timers.timers["aoe:dragon roar"]
        self.assertEqual((t.label, t.target, t.end - t.start), ("Dragon Roar", "Lord Nagafen", 36))
        self.e.tick(1029)
        self.assertEqual(self.events(), [])
        got = []
        for now in (1030, 1031, 1032, 1033, 1034, 1035):
            self.e.tick(now)
            got += self.events()
        self.assertEqual([x.text for x in got], [f"Dragon Roar in {n} Seconds!" for n in (6, 5, 4, 3, 2, 1)])
        self.assertEqual([x.say if x.speak else None for x in got],
                         ["Dragon Roar in 6 seconds", None, "4 seconds", None, "2", "1"])
        self.assertEqual({x.key for x in got}, {"roar"})

    def test_roar_seen_by_the_whole_raid_counts_once(self):
        self.e.handle(1000, "You are blasted by a mighty roar.")
        self.e.handle(1001, "Bob is blasted by a mighty roar.")
        self.e.handle(1002, "Carl is blasted by a mighty roar.")
        self.assertEqual(len([a for a in self.events() if a.kind == "roar"]), 1)
        self.e.handle(1040, "You are blasted by a mighty roar.")      # the next one
        self.assertEqual(len([a for a in self.events() if a.kind == "roar"]), 1)

    def test_other_raid_spells_get_a_timer_but_no_countdown(self):
        self.e.handle(1000, "You are scorched by lava.")
        self.assertEqual([a for a in self.events() if a.kind == "roar"], [])
        t = self.e.timers.timers["aoe:lava breath"]
        self.assertEqual((t.target, t.end - t.start), ("Lord Nagafen", 20))

    def test_not_a_raid_zone_means_no_raid_alerts(self):
        self.e.zone = "East Commonlands"
        self.e.handle(1000, "You are blasted by a mighty roar.")
        self.assertEqual([a for a in self.events() if a.kind == "roar"], [])

    def test_stale_scheduled_alerts_are_dropped(self):
        self.e.handle(1000, "You are blasted by a mighty roar.")
        self.events()
        self.e.tick(5000)                      # hours later: nothing is announced late
        self.assertEqual(self.events(), [])
