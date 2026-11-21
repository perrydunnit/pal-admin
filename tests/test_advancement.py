"""Tests for the advancement metric and the level history it needs.

The rate is a regression over sparse observations, so it can be wrong in ways
that still produce a plausible-looking number. These pin the shape: a player on
a clean logarithmic curve, a recreated character whose level drops, and the case
where there is not enough history to say anything at all.
"""

import datetime as dt
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.app_env import import_app  # noqa: E402

paladmin, APP_IMPORT_ERROR = import_app()

needs_app = unittest.skipUnless(
    paladmin is not None, f"app.py is not importable here: {APP_IMPORT_ERROR}")

START = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def at(hours):
    return START + dt.timedelta(hours=hours)


def iso(hours):
    return at(hours).isoformat()


def sample(hours, level):
    return {"ts": iso(hours), "level": level}


def open_session(start_hours):
    return {"started_at": iso(start_hours), "ended_at": None}


@needs_app
class AdvancementTests(unittest.TestCase):
    def test_levels_per_doubling_matches_a_clean_log_curve(self):
        # Level = 10 + 2*log2(hours + 1), sampled where each doubling lands.
        levels = {0: 10, 1: 12, 3: 14, 7: 16, 15: 18, 31: 20}
        samples = [sample(h, level) for h, level in levels.items()]
        result = paladmin.advancement(samples, [open_session(0)], now=at(31))

        self.assertIsNotNone(result)
        self.assertEqual(result["level"], 20)
        self.assertEqual(result["level_ups"], 5)
        self.assertAlmostEqual(result["levels_per_doubling"], 2.0, delta=0.02)
        # A +2 levels per doubling curve is e^(ln2/2) = sqrt(2) per level.
        self.assertAlmostEqual(result["curve_growth"], 2 ** 0.5, delta=0.02)
        # At h=31 the next level costs about 32/slope hours.
        self.assertAlmostEqual(result["hours_per_level"], 11.1, delta=0.2)

    def test_playtime_uses_sessions_not_wall_clock(self):
        # The same six samples, but only 8 of the 31 wall-clock hours were played.
        sessions = [
            {"started_at": iso(0), "ended_at": iso(4)},
            {"started_at": iso(20), "ended_at": iso(24)},
            {"started_at": iso(27), "ended_at": iso(31)},
        ]
        samples = [sample(h, lvl) for h, lvl in
                   {0: 10, 1: 12, 3: 14, 7: 16, 15: 18, 31: 20}.items()]
        idle = paladmin.advancement(samples, [open_session(0)], now=at(31))
        played = paladmin.advancement(samples, sessions, now=at(31))

        self.assertAlmostEqual(played["hours"], 12.0, delta=0.01)
        self.assertLess(played["hours"], idle["hours"])
        self.assertLess(played["hours_per_level"], idle["hours_per_level"])

    def test_a_dropped_level_starts_a_new_run(self):
        samples = [
            sample(0, 30), sample(1, 31), sample(3, 32), sample(5, 33),
            sample(40, 1), sample(41, 2), sample(43, 3), sample(47, 4),
        ]
        result = paladmin.advancement(samples, [open_session(0)], now=at(47))

        self.assertIsNotNone(result)
        self.assertEqual(result["level"], 4)
        self.assertEqual(result["observed_since"], iso(40))
        self.assertEqual(result["level_ups"], 3)

    def test_insufficient_history_returns_none(self):
        session = [open_session(0)]
        self.assertIsNone(paladmin.advancement([sample(0, 10)], session, at(1)))
        # No level-ups: a flat line is not a rate.
        self.assertIsNone(paladmin.advancement(
            [sample(0, 10), sample(1, 10), sample(2, 10)], session, at(2)))
        # One level-up is not enough to fit a curve.
        self.assertIsNone(paladmin.advancement(
            [sample(0, 10), sample(1, 11)], session, at(1)))

    def test_offline_gaps_do_not_count_as_playtime(self):
        session = [open_session(0)]
        self.assertAlmostEqual(
            paladmin.active_hours_before(session, at(5), at(9)), 5.0, delta=0.01)
        self.assertAlmostEqual(
            paladmin.active_hours_before(session, at(20), at(9)), 9.0, delta=0.01)


@needs_app
class RecordLevelTests(unittest.TestCase):
    """The ingestion guard: level samples must not grow with the poll rate."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE level_samples (player_id TEXT NOT NULL, ts TEXT NOT NULL,"
            " level INTEGER NOT NULL, PRIMARY KEY (player_id, ts))")

    def tearDown(self):
        self.conn.close()

    def rows(self):
        return [tuple(r) for r in self.conn.execute(
            "SELECT player_id, level FROM level_samples ORDER BY ts")]

    def test_repeats_inside_one_session_are_dropped(self):
        started = at(0)
        self.assertTrue(paladmin.record_level(
            self.conn, "p", 12, at(0.01), started))
        for minutes in (1, 2, 10):
            self.assertFalse(paladmin.record_level(
                self.conn, "p", 12, at(minutes / 60), started))
        self.assertEqual(self.rows(), [("p", 12)])

    def test_a_change_is_recorded(self):
        started = at(0)
        paladmin.record_level(self.conn, "p", 12, at(0.01), started)
        self.assertTrue(paladmin.record_level(self.conn, "p", 13, at(2), started))
        self.assertEqual(self.rows(), [("p", 12), ("p", 13)])

    def test_a_new_session_records_the_current_level(self):
        paladmin.record_level(self.conn, "p", 13, at(0.01), at(0))
        self.assertFalse(paladmin.record_level(self.conn, "p", 13, at(1), at(0)))
        self.assertTrue(paladmin.record_level(self.conn, "p", 13, at(4), at(4)))
        self.assertEqual(self.rows(), [("p", 13), ("p", 13)])

    def test_null_levels_are_ignored(self):
        # The API reports no level while a character is still loading.
        self.assertFalse(paladmin.record_level(self.conn, "p", None, at(0.01), at(0)))
        self.assertFalse(paladmin.record_level(self.conn, "p", "", at(0.02), at(0)))
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
