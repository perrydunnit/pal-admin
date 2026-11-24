"""Tests for the advancement metric and the level history behind it.

The metric has one claim to defend: it must describe the player, not their
position on the curve. A level-40 and a level-70 player who advance at the same
relative pace have to come out with the same number, or the column just repeats
the level. These pin that, plus the ingestion guard that keeps transient API
reports out of `level_samples`.
"""

import datetime as dt
import math
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


def hours_for(level, per_efold=12.0):
    """Playtime a character starting at level 1 needs to reach `level`."""
    return math.exp((level - 1) / per_efold) - 1.0


@needs_app
class AdvancementTests(unittest.TestCase):
    def test_recovers_the_slope_it_was_built_from(self):
        result = paladmin.advancement(40, hours_for(40, per_efold=12.0))

        self.assertAlmostEqual(
            result["levels_per_doubling"], 12.0 * math.log(2), delta=0.05)

    def test_the_number_does_not_move_with_level(self):
        # Same per-e-fold pace at four very different levels: one number.
        values = [paladmin.advancement(level, hours_for(level))["levels_per_doubling"]
                  for level in (10, 30, 55, 79)]

        for value in values:
            self.assertAlmostEqual(value, values[0], delta=0.01)

    def test_a_fast_player_outranks_a_slow_one_at_the_same_level(self):
        fast = paladmin.advancement(60, hours_for(60, per_efold=18.0))
        slow = paladmin.advancement(60, hours_for(60, per_efold=10.0))

        self.assertGreater(fast["levels_per_doubling"], slow["levels_per_doubling"])
        # Level 60 in less time must not be penalised for being level 60.
        self.assertEqual(fast["level"], slow["level"])

    def test_insufficient_data_returns_none(self):
        self.assertIsNone(paladmin.advancement(None, 10))
        self.assertIsNone(paladmin.advancement(1, 10))
        # Under an hour is still the opening rush, where levels are minutes
        # apart and the fit says nothing about the player.
        self.assertIsNone(paladmin.advancement(12, 0.5))
        self.assertIsNone(paladmin.advancement(12, 0))


@needs_app
class ObservedLevelTests(unittest.TestCase):
    def test_a_transient_level_1_does_not_lower_the_reported_level(self):
        # The live site recorded 76 -> 1 -> 76 inside one minute; that row is noise.
        samples = [{"level": 76}, {"level": 1}, {"level": 76}]

        self.assertEqual(paladmin.observed_level({"level": 1}, samples), 76)

    def test_falls_back_to_the_stored_level(self):
        self.assertEqual(paladmin.observed_level({"level": 42}, []), 42)
        self.assertIsNone(paladmin.observed_level({"level": None}, []))


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
        self.assertTrue(paladmin.record_level(self.conn, "p", 12, START, START))
        self.assertFalse(paladmin.record_level(
            self.conn, "p", 12, START + dt.timedelta(minutes=10), START))
        self.assertEqual(self.rows(), [("p", 12)])

    def test_a_change_is_recorded(self):
        paladmin.record_level(self.conn, "p", 12, START, START)
        self.assertTrue(paladmin.record_level(
            self.conn, "p", 13, START + dt.timedelta(hours=2), START))
        self.assertEqual(self.rows(), [("p", 12), ("p", 13)])

    def test_a_drop_inside_a_session_is_noise(self):
        # The 76 -> 1 -> 76 sequence the API produces while a character loads.
        # Recording it would restart the player's history.
        paladmin.record_level(self.conn, "p", 76, START, START)
        self.assertFalse(paladmin.record_level(
            self.conn, "p", 1, START + dt.timedelta(seconds=10), START))
        self.assertEqual(self.rows(), [("p", 76)])

    def test_a_drop_between_sessions_is_a_new_character(self):
        paladmin.record_level(self.conn, "p", 76, START, START)
        second = START + dt.timedelta(hours=5)
        self.assertTrue(paladmin.record_level(self.conn, "p", 1, second, second))
        self.assertEqual(self.rows(), [("p", 76), ("p", 1)])

    def test_a_new_session_records_the_current_level(self):
        paladmin.record_level(self.conn, "p", 13, START, START)
        self.assertFalse(paladmin.record_level(
            self.conn, "p", 13, START + dt.timedelta(minutes=5), START))
        third = START + dt.timedelta(hours=4)
        self.assertTrue(paladmin.record_level(self.conn, "p", 13, third, third))
        self.assertEqual(self.rows(), [("p", 13), ("p", 13)])

    def test_null_levels_are_ignored(self):
        # The API reports no level while a character is still loading.
        self.assertFalse(paladmin.record_level(self.conn, "p", None, START, START))
        self.assertFalse(paladmin.record_level(self.conn, "p", "", START, START))
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
