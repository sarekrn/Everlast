"""Boats, Ring War and the CH chain."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from eqtool_native import events  # noqa: E402
from eqtool_native.engine import Engine  # noqa: E402
from eqtool_native.settings import Settings  # noqa: E402
from eqtool_native.spells import SpellDB  # noqa: E402

T0 = 1_000_000.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Settings(os.path.join(self.tmp.name, "s.json"))
        self.e = Engine(self.s, SpellDB())

    def tearDown(self):
        self.tmp.cleanup()

    def timers(self, kind, now=None):
        now = T0 if now is None else now
        return {t.key: t for t in self.e.timers.active(now) if t.kind == kind}

    def alerts(self):
        out = []
        while self.e.events:
            k, d = self.e.events.popleft()
            if k == "alert":
                out.append((d.kind, d.text, d.say))
        return out


class BoatTests(Base):
    BARGE = "Rack Stonebelly shouts, 'Da Barrel Barge will be here soon soon!'"

    def test_data_loaded(self):
        self.assertEqual(len(self.e.boats.legs), 7)
        self.assertEqual({l["name"] for l in self.e.boats.legs},
                         {"Barrel Barge", "Bloated Belly", "Maiden's Voyage", "NRo-Iceclad boat"})

    def test_announcement_starts_both_ports(self):
        self.e.handle(T0, self.BARGE)
        t = self.timers("boat")
        self.assertEqual(len(t), 2)
        self.assertAlmostEqual(t["boat:0:oasis"].end - T0, 119, delta=0.01)
        self.assertAlmostEqual(t["boat:0:timorous"].end - T0, 510, delta=0.01)
        self.assertEqual(t["boat:0:oasis"].target, "Barrel Barge")

    def test_single_leg_boat(self):
        self.e.handle(T0, "Rack Stonebelly shouts, 'Da Bloated Belly be leaving da Overdere now!'")
        t = self.timers("boat")
        self.assertEqual(list(t), ["boat:1:overthere"])
        self.assertAlmostEqual(t["boat:1:overthere"].end - T0, 1975, delta=0.01)

    def test_timers_renew_for_the_next_lap_and_alert(self):
        self.e.handle(T0, self.BARGE)
        self.alerts()
        self.e.tick(T0 + 130)                       # the Oasis dock time (119 s) has passed
        a = self.alerts()
        self.assertEqual([x[0] for x in a], ["boat"])
        self.assertIn("Oasis arrival", a[0][1])
        self.e.tick(T0 + 131)
        t = self.timers("boat", T0 + 131)
        # next lap: trip - elapsed + dock = 779.75 - 131 + 119
        self.assertAlmostEqual(t["boat:0:oasis"].end - (T0 + 131), 779.75 - 131 + 119, delta=2)

    def test_stale_boat_is_dropped(self):
        self.e.handle(T0, self.BARGE)
        self.e.tick(T0 + 7300)
        self.assertEqual(self.e.boat_seen, {})

    def test_other_announcements(self):
        self.e.handle(T0, "Frankel the Pirate says 'Thar she be mates. All aboard thats goin aboard!'")
        self.assertEqual(len(self.timers("boat")), 2)
        self.e.handle(T0, "Glisse Bluesea shouts 'The Maiden's Voyage is now ready to be boarded. "
                          "Please form an orderly line to the shuttles, and remember, no pushing!'")
        self.assertEqual(len(self.timers("boat")), 4)

    def test_off_switch(self):
        self.s["boat_timers"] = False
        self.e.handle(T0, self.BARGE)
        self.assertEqual(self.timers("boat"), {})


class RingWarTests(Base):
    SHOUT = "Seneschal Aldikar shouts, 'TROOPS, TAKE YOUR POSITIONS!'"

    def test_schedule(self):
        sched = events.ring_war_schedule(self.e.event_data["ringwar"])
        self.assertEqual(len(sched), 24)
        self.assertEqual(sched[0], (1, "Round 1", 210.0))
        self.assertEqual(sched[6], (1, "Round 7", 1470.0))
        self.assertEqual(sched[7], (1, "-- Break --", 1770.0))
        self.assertEqual(sched[8], (2, "Round 1", 1980.0))
        self.assertEqual(sched[-1], (3, "-- Break --", 5310.0))

    def test_shout_creates_timers_and_alerts(self):
        self.e.handle(T0, self.SHOUT)
        t = self.timers("ringwar")
        self.assertEqual(len(t), 24)
        self.assertAlmostEqual(t["ringwar:1:Round 1"].end - T0, 210, delta=0.01)
        self.e.tick(T0 + 215)
        a = self.alerts()
        self.assertEqual(a[0][0], "ringwar")
        self.assertIn("Round 1", a[0][1])

    def test_shout_without_quotes_and_restart(self):
        self.e.handle(T0, "Seneschal Aldikar shouts, TROOPS, TAKE YOUR POSITIONS!")
        self.e.handle(T0 + 100, self.SHOUT)
        t = self.timers("ringwar", T0 + 100)
        self.assertEqual(len(t), 24)
        self.assertAlmostEqual(t["ringwar:1:Round 1"].end - (T0 + 100), 210, delta=0.01)

    def test_off_switch(self):
        self.s["ringwar_timers"] = False
        self.e.handle(T0, self.SHOUT)
        self.assertEqual(self.timers("ringwar"), {})


class ChParseTests(unittest.TestCase):
    def p(self, msg, tag=""):
        return events.parse_ch(msg, T0, tag)

    def test_standard_call(self):
        c = self.p("Aaa tells the raid, '001 CH -- Beefwich'")
        self.assertEqual((c.caster, c.position, c.recipient), ("Aaa", "001", "Beefwich"))

    def test_you_and_letter_position(self):
        c = self.p("You tell your raid, 'A CH Tank'")
        self.assertEqual((c.caster, c.position, c.recipient), ("You", "A", "Tank"))

    def test_other_channels_and_rch(self):
        self.assertIsNotNone(self.p("Bob tells the guild, '003 RCH Cleric'"))
        self.assertIsNotNone(self.p("Bob says out of character, '002 CH Tank'"))

    def test_not_a_call(self):
        for m in ("Bob tells the raid, 'need a ch on the tank please now'",
                  "Bob tells the raid, 'much chain here'",
                  "Bob tells the raid, 'hello there'",
                  "Bob hits a rat for 5 points of damage.",
                  "Bob tells the raid, '001 CH'"):
            self.assertIsNone(self.p(m), m)

    def test_default_position_and_tag(self):
        self.assertEqual(self.p("Bob tells the raid, 'CH Tank'").position, "000")
        self.assertIsNone(self.p("Bob tells the raid, '001 CH Tank'", tag="GG"))
        c = self.p("Bob tells the raid, 'GG 001 CH Tank'", tag="GG")
        self.assertEqual((c.position, c.recipient), ("001", "Tank"))


class ChainTests(Base):
    def say(self, who, text, dt=0):
        verb = "tell your raid" if who == "You" else "tells the raid"
        self.e.handle(T0 + dt, f"{who} {verb}, '{text}'")

    def test_warns_when_slot_before_yours_is_called(self):
        self.say("Bob", "001 CH -- Tank", 0)
        self.say("You", "002 CH -- Tank", 3)
        self.say("Cat", "003 CH -- Tank", 6)
        self.alerts()
        self.say("Bob", "001 CH -- Tank", 9)
        a = self.alerts()
        self.assertEqual(a, [("ch_warn", "CH WARNING", "C H warning")])

    def test_wraps_around_for_first_slot(self):
        self.say("You", "001 CH -- Tank", 0)
        self.say("Bob", "002 CH -- Tank", 3)
        self.say("Cat", "003 CH -- Tank", 6)
        self.alerts()
        self.say("Cat", "003 CH -- Tank", 9)       # the end of the chain called again
        self.assertEqual([x[0] for x in self.alerts()], ["ch_warn"])

    def test_no_warning_before_you_join(self):
        self.say("Bob", "001 CH -- Tank", 0)
        self.say("Cat", "002 CH -- Tank", 3)
        self.assertEqual(self.alerts(), [])

    def test_letters(self):
        self.say("You", "C CH -- Tank", 0)
        self.say("Bob", "B CH -- Tank", 3)
        self.assertEqual([x[0] for x in self.alerts()], ["ch_warn"])

    def test_chains_are_per_target_and_expire(self):
        self.say("You", "002 CH -- Tank", 0)
        self.say("Bob", "001 CH -- Cleric", 3)      # a different chain
        self.assertEqual(self.alerts(), [])
        self.say("Bob", "001 CH -- Tank", 100)      # the old Tank chain has been forgotten
        self.assertEqual(self.alerts(), [])

    def test_call_timer(self):
        self.say("Bob", "001 CH -- Tank", 0)
        t = self.timers("ch", T0 + 1)
        self.assertEqual(len(t), 1)
        k = next(iter(t))
        self.assertEqual((t[k].target, t[k].label), ("Tank", "001 Bob"))
        self.assertAlmostEqual(t[k].end - T0, 10, delta=0.01)

    def test_off_switch(self):
        self.s["ch_chain"] = False
        self.say("Bob", "001 CH -- Tank")
        self.assertEqual(self.timers("ch", T0 + 1), {})


if __name__ == "__main__":
    unittest.main()
