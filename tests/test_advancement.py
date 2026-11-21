"""Tests for the EXP-per-hour metric and the level history it needs.

EXP/hour is inferred from level changes against a fixed curve, so the two ways
it can lie are a bad curve and a bad level of observation. These pin the curve
against known totals, a constant rate coming back as a constant rate, and the
cases where there is not enough history to say anything at all.
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
class LevelTableTests(unittest.TestCase):
    def test_cumulative_totals_match_the_wiki(self):
        self.assertEqual(paladmin.level_exp(1), 0)
        self.assertEqual(paladmin.level_exp(2), 50)
        self.assertEqual(paladmin.level_exp(14), 16_610)
        self.assertEqual(paladmin.level_exp(50), 2_378_134)
        self.assertEqual(paladmin.level_exp(60), 6_498_533)
        self.assertEqual(paladmin.level_exp(80), 45_859_908)

    def test_every_level_costs_more_than_the_last(self):
        for earlier, later in zip(paladmin.LEVEL_COST, paladmin.LEVEL_COST[1:]):
            self.assertGreater(later, earlier)

    def test_extrapolates_past_the_cap(self):
        # A modded server can raise the cap; the number must keep growing.
        self.assertGreater(paladmin.level_exp(90), paladmin.level_exp(80))
        self.assertLess(paladmin.level_exp(90) - paladmin.level_exp(89),
                        paladmin.level_exp(91) - paladmin.level_exp(90))


def at_level(rate, start_level, level):
    """Hours of play at `rate` EXP/hour to go from `start_level` to `level`."""
    return (paladmin.level_exp(level) - paladmin.level_exp(start_level)) / rate


@needs_app
class AdvancementTests(unittest.TestCase):
    RATE = 10_000
    START = 30

    def samples_for(self, levels):
        return ([sample(0, self.START)]
                + [sample(at_level(self.RATE, self.START, lvl), lvl) for lvl in levels])

    def test_a_constant_rate_comes_back_constant_across_the_curve(self):
        samples = self.samples_for([31, 32, 33, 34, 35, 36])
        result = paladmin.advancement(samples, [open_session(0)], now=at(40))

        self.assertIsNotNone(result)
        self.assertEqual(result["level"], 36)
        self.assertEqual(result["level_ups"], 6)
        # The last levels cost 5-10x the first ones, and the rate must not move.
        self.assertAlmostEqual(result["exp_per_hour"], self.RATE, delta=self.RATE * 0.01)

    def test_the_run_starts_at_the_first_level_up_not_at_login(self):
        samples = self.samples_for([31, 32, 33])
        result = paladmin.advancement(samples, [open_session(0)], now=at(40))

        self.assertEqual(result["observed_since"], samples[1]["ts"])
        self.assertEqual(result["samples"], 4)

    def test_playtime_uses_sessions_not_wall_clock(self):
        # Level 31 lands in the first session, level 32 in the second. The 2 h
        # gap between them must not be charged as playtime.
        sessions = [
            {"started_at": iso(0), "ended_at": iso(5)},
            {"started_at": iso(7), "ended_at": iso(13)},
        ]
        samples = [
            sample(0, self.START),
            sample(3.4546, 31),
            sample(9.2775, 32),
        ]
        result = paladmin.advancement(samples, sessions, now=at(13))

        self.assertAlmostEqual(result["hours"], 3.8, delta=0.05)
        self.assertAlmostEqual(result["exp_per_hour"], self.RATE, delta=self.RATE * 0.01)

    def test_a_dropped_level_starts_a_new_run(self):
        samples = [
            sample(0, 40), sample(1, 41), sample(2, 42),
            sample(10, 1), sample(10.1, 2), sample(10.2, 3),
        ]
        result = paladmin.advancement(samples, [open_session(0)], now=at(12))

        self.assertIsNotNone(result)
        self.assertEqual(result["level"], 3)
        self.assertEqual(result["observed_since"], iso(10.1))

    def test_insufficient_history_returns_none(self):
        session = [open_session(0)]
        self.assertIsNone(paladmin.advancement([sample(0, 10)], session, at(1)))
        # No level-ups: there is no rate to measure.
        self.assertIsNone(paladmin.advancement(
            [sample(0, 10), sample(1, 10), sample(2, 10)], session, at(2)))
        # One level-up only brackets that level; it cannot give a rate.
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
