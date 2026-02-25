"""Tests for core/scheduler.py."""

from __future__ import annotations

import json
import threading
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from core.scheduler import (
    ParsedSchedule,
    ScheduledTaskRunner,
    SchedulerState,
    parse_schedule_expr,
    _HOUR_ANY,
)


# ── parse_schedule_expr ────────────────────────────────────────────────────────

class ParseScheduleExprTests(unittest.TestCase):

    # ---- täglich ----

    def test_taeglich_simple(self):
        p = parse_schedule_expr("täglich 09:00")
        self.assertIsNotNone(p)
        self.assertEqual(p.hour, 9)
        self.assertEqual(p.minute, 0)
        self.assertIsNone(p.weekday)

    def test_taeglich_leading_trailing_space(self):
        p = parse_schedule_expr("  täglich 23:59  ")
        self.assertIsNotNone(p)
        self.assertEqual(p.hour, 23)
        self.assertEqual(p.minute, 59)

    def test_taeglich_uppercase(self):
        p = parse_schedule_expr("TÄGLICH 08:30")
        self.assertIsNotNone(p)
        self.assertEqual(p.hour, 8)
        self.assertEqual(p.minute, 30)

    def test_taeglich_invalid_time(self):
        self.assertIsNone(parse_schedule_expr("täglich 25:00"))
        self.assertIsNone(parse_schedule_expr("täglich 12:60"))

    # ---- stündlich ----

    def test_stuendlich(self):
        p = parse_schedule_expr("stündlich")
        self.assertIsNotNone(p)
        self.assertEqual(p.minute, 0)
        self.assertEqual(p.hour, _HOUR_ANY)
        self.assertIsNone(p.weekday)

    # ---- German day names ----

    def test_montags(self):
        p = parse_schedule_expr("montags 10:15")
        self.assertIsNotNone(p)
        self.assertEqual(p.weekday, 0)
        self.assertEqual(p.hour, 10)
        self.assertEqual(p.minute, 15)

    def test_freitags(self):
        p = parse_schedule_expr("freitags 17:00")
        self.assertIsNotNone(p)
        self.assertEqual(p.weekday, 4)

    def test_sonntags(self):
        p = parse_schedule_expr("sonntags 08:00")
        self.assertIsNotNone(p)
        self.assertEqual(p.weekday, 6)

    # ---- crontab ----

    def test_crontab_simple(self):
        p = parse_schedule_expr("0 9 * * *")
        self.assertIsNotNone(p)
        self.assertEqual(p.minute, 0)
        self.assertEqual(p.hour, 9)
        self.assertIsNone(p.weekday)

    def test_crontab_with_weekday(self):
        p = parse_schedule_expr("30 8 * * 1")
        self.assertIsNotNone(p)
        self.assertEqual(p.minute, 30)
        self.assertEqual(p.hour, 8)
        self.assertEqual(p.weekday, 1)

    def test_crontab_hourly(self):
        p = parse_schedule_expr("0 * * * *")
        self.assertIsNotNone(p)
        self.assertEqual(p.minute, 0)
        self.assertEqual(p.hour, _HOUR_ANY)

    def test_crontab_invalid(self):
        self.assertIsNone(parse_schedule_expr("60 9 * * *"))
        self.assertIsNone(parse_schedule_expr("0 25 * * *"))
        self.assertIsNone(parse_schedule_expr("garbage"))

    def test_unknown_expr_returns_none(self):
        self.assertIsNone(parse_schedule_expr("every 5 minutes"))
        self.assertIsNone(parse_schedule_expr(""))


# ── ParsedSchedule.matches ─────────────────────────────────────────────────────

class ParsedScheduleMatchesTests(unittest.TestCase):

    def _dt(self, weekday=0, hour=9, minute=0):
        # 2026-02-23 = Monday (weekday=0)
        base = datetime(2026, 2, 23, hour, minute, 0)
        # advance by weekday
        from datetime import timedelta
        return base + timedelta(days=weekday)

    def test_daily_matches(self):
        p = parse_schedule_expr("täglich 09:00")
        self.assertTrue(p.matches(self._dt(hour=9, minute=0)))

    def test_daily_no_match_wrong_hour(self):
        p = parse_schedule_expr("täglich 09:00")
        self.assertFalse(p.matches(self._dt(hour=10, minute=0)))

    def test_daily_no_match_wrong_minute(self):
        p = parse_schedule_expr("täglich 09:00")
        self.assertFalse(p.matches(self._dt(hour=9, minute=1)))

    def test_weekday_matches_correct_day(self):
        p = parse_schedule_expr("montags 10:00")
        monday = self._dt(weekday=0, hour=10, minute=0)
        self.assertTrue(p.matches(monday))

    def test_weekday_no_match_wrong_day(self):
        p = parse_schedule_expr("montags 10:00")
        tuesday = self._dt(weekday=1, hour=10, minute=0)
        self.assertFalse(p.matches(tuesday))

    def test_hourly_matches_any_hour(self):
        p = parse_schedule_expr("stündlich")
        self.assertTrue(p.matches(self._dt(hour=9, minute=0)))
        self.assertTrue(p.matches(self._dt(hour=14, minute=0)))

    def test_hourly_no_match_wrong_minute(self):
        p = parse_schedule_expr("stündlich")
        self.assertFalse(p.matches(self._dt(hour=9, minute=30)))


# ── SchedulerState ─────────────────────────────────────────────────────────────

class SchedulerStateTests(unittest.TestCase):

    def test_empty_on_missing_file(self):
        state = SchedulerState.load("/nonexistent/path/schedules.json")
        self.assertEqual(state.entries, {})

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "schedules.json")
            state = SchedulerState()
            state.entries["abc"] = {
                "room_id": "!room:example.org",
                "expr": "täglich 09:00",
                "task": "Do something",
                "created_by": "@alice:example.org",
                "created_at": "2026-02-25T10:00:00",
                "last_fired": None,
            }
            state.save(path)

            loaded = SchedulerState.load(path)
            self.assertIn("abc", loaded.entries)
            self.assertEqual(loaded.entries["abc"]["task"], "Do something")

    def test_corrupted_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "schedules.json")
            Path(path).write_text("not json", encoding="utf-8")
            state = SchedulerState.load(path)
            self.assertEqual(state.entries, {})


# ── ScheduledTaskRunner ────────────────────────────────────────────────────────

class ScheduledTaskRunnerTests(unittest.TestCase):

    def _make_runner(self, tmpdir, fire_fn=None):
        path = str(Path(tmpdir) / "schedules.json")
        if fire_fn is None:
            fire_fn = lambda sid, rid, task: None
        return ScheduledTaskRunner(state_file=path, fire_fn=fire_fn), path

    def test_add_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir)
            result = runner.add(
                room_id="!room:example.org",
                expr="täglich 09:00",
                task="Daily review",
                created_by="@alice:example.org",
            )
            self.assertIsNotNone(result)
            sched_id, parsed = result
            self.assertEqual(len(sched_id), 8)  # uuid hex[:8]

            entries = runner.list_for_room("!room:example.org")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["task"], "Daily review")

    def test_add_invalid_expr_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir)
            result = runner.add(
                room_id="!room:example.org",
                expr="garbage expr",
                task="Do stuff",
                created_by="@alice:example.org",
            )
            self.assertIsNone(result)

    def test_remove(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir)
            result = runner.add(
                room_id="!room:example.org",
                expr="täglich 09:00",
                task="task",
                created_by="@alice:example.org",
            )
            sched_id = result[0]
            self.assertTrue(runner.remove(sched_id))
            self.assertFalse(runner.remove(sched_id))  # already removed

    def test_remove_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir)
            self.assertFalse(runner.remove("doesnotexist"))

    def test_list_for_room_filters_other_rooms(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir)
            runner.add(room_id="!room1:example.org", expr="täglich 09:00", task="A", created_by="@u:e")
            runner.add(room_id="!room2:example.org", expr="täglich 10:00", task="B", created_by="@u:e")

            r1 = runner.list_for_room("!room1:example.org")
            r2 = runner.list_for_room("!room2:example.org")
            self.assertEqual(len(r1), 1)
            self.assertEqual(r1[0]["task"], "A")
            self.assertEqual(len(r2), 1)
            self.assertEqual(r2[0]["task"], "B")

    def test_state_persisted_on_add(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, path = self._make_runner(tmpdir)
            runner.add(room_id="!r:e", expr="täglich 09:00", task="task", created_by="@u:e")

            # Reload from disk
            runner2 = ScheduledTaskRunner(state_file=path, fire_fn=lambda *a: None)
            entries = runner2.list_for_room("!r:e")
            self.assertEqual(len(entries), 1)

    def test_tick_fires_matching_schedule(self):
        fired = []
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir, fire_fn=lambda sid, rid, task: fired.append(sid))
            runner.add(room_id="!r:e", expr="täglich 09:00", task="task", created_by="@u:e")

            now = datetime(2026, 2, 25, 9, 0, 0)
            runner._tick(now)

            self.assertEqual(len(fired), 1)

    def test_tick_no_double_fire_same_minute(self):
        fired = []
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir, fire_fn=lambda sid, rid, task: fired.append(sid))
            runner.add(room_id="!r:e", expr="täglich 09:00", task="task", created_by="@u:e")

            now = datetime(2026, 2, 25, 9, 0, 0)
            runner._tick(now)
            runner._tick(now)  # same minute — must not fire again

            self.assertEqual(len(fired), 1)

    def test_tick_fires_next_day(self):
        fired = []
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir, fire_fn=lambda sid, rid, task: fired.append(sid))
            runner.add(room_id="!r:e", expr="täglich 09:00", task="task", created_by="@u:e")

            runner._tick(datetime(2026, 2, 25, 9, 0, 0))
            runner._tick(datetime(2026, 2, 26, 9, 0, 0))  # next day

            self.assertEqual(len(fired), 2)

    def test_tick_no_fire_wrong_time(self):
        fired = []
        with tempfile.TemporaryDirectory() as tmpdir:
            runner, _ = self._make_runner(tmpdir, fire_fn=lambda sid, rid, task: fired.append(sid))
            runner.add(room_id="!r:e", expr="täglich 09:00", task="task", created_by="@u:e")

            runner._tick(datetime(2026, 2, 25, 10, 0, 0))  # wrong hour

            self.assertEqual(len(fired), 0)


if __name__ == "__main__":
    unittest.main()
