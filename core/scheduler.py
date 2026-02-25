"""Scheduled task runner for DevAgent.

Expressions supported (case-insensitive):
  "täglich HH:MM"             — every day at HH:MM local time
  "stündlich"                 — every full hour (:00)
  "montags HH:MM"             — every Monday at HH:MM
  "dienstags/mittwochs/...    HH:MM"
  "0 9 * * *"                 — crontab: min hour dom mon dow
                                dom/mon ignored (wildcard only);
                                dow: 0=Mon…6=Sun (wildcard=any day)
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("devagent.scheduler")

# German day-name → weekday int (Monday=0)
_DE_DAYS: dict[str, int] = {
    "montags": 0,
    "dienstags": 1,
    "mittwochs": 2,
    "donnerstags": 3,
    "freitags": 4,
    "samstags": 5,
    "sonntags": 6,
}

_HOUR_ANY = -1   # matches every hour
_MIN_ANY  = -1   # matches every minute (rarely useful)


# ── Cron expression ────────────────────────────────────────────────────────────

class ParsedSchedule:
    """Normalised cron: (minute, hour, weekday), all -1/None = wildcard."""

    def __init__(self, minute: int, hour: int, weekday: int | None, raw_expr: str) -> None:
        self.minute   = minute
        self.hour     = hour
        self.weekday  = weekday   # None = any day
        self.raw_expr = raw_expr

    def matches(self, now: datetime) -> bool:
        if self.minute != _MIN_ANY and now.minute != self.minute:
            return False
        if self.hour != _HOUR_ANY and now.hour != self.hour:
            return False
        if self.weekday is not None and now.weekday() != self.weekday:
            return False
        return True

    def human_readable(self) -> str:
        return self.raw_expr


def parse_schedule_expr(expr: str) -> ParsedSchedule | None:
    """Parse a schedule expression.  Returns None on failure."""
    s = expr.strip()
    sl = s.lower()

    # "stündlich"
    if sl in ("stündlich", "stundlich", "stündlich"):
        return ParsedSchedule(minute=0, hour=_HOUR_ANY, weekday=None, raw_expr=s)

    # "täglich HH:MM"
    if sl.startswith(("täglich ", "taglich ")):
        return _parse_time_suffix(s.split(None, 1)[1], weekday=None, raw_expr=s)

    # "wöchentlich HH:MM" → Monday
    if sl.startswith(("wöchentlich ", "wochentlich ")):
        return _parse_time_suffix(s.split(None, 1)[1], weekday=0, raw_expr=s)

    # German day names: "montags HH:MM" etc.
    for day_name, weekday in _DE_DAYS.items():
        if sl.startswith(day_name + " "):
            return _parse_time_suffix(s.split(None, 1)[1], weekday=weekday, raw_expr=s)

    # 5-part crontab: "MIN HOUR DOM MON DOW"
    parts = sl.split()
    if len(parts) == 5:
        return _parse_crontab(parts, raw_expr=s)

    return None


def _parse_time_suffix(time_str: str, *, weekday: int | None, raw_expr: str) -> ParsedSchedule | None:
    """Parse "HH:MM" (possibly leading/trailing whitespace)."""
    try:
        h_str, m_str = time_str.strip().split(":", 1)
        hour, minute = int(h_str), int(m_str)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return ParsedSchedule(minute=minute, hour=hour, weekday=weekday, raw_expr=raw_expr)
    except (ValueError, AttributeError):
        return None


def _parse_crontab(parts: list[str], *, raw_expr: str) -> ParsedSchedule | None:
    """Parse 5-part crontab.  Only simple numeric values + '*' supported."""
    try:
        min_p, hour_p, _dom, _mon, dow_p = parts

        minute = _MIN_ANY  if min_p  == "*" else int(min_p)
        hour   = _HOUR_ANY if hour_p == "*" else int(hour_p)

        if minute != _MIN_ANY and not (0 <= minute <= 59):
            return None
        if hour != _HOUR_ANY and not (0 <= hour <= 23):
            return None

        if dow_p in ("*", "?"):
            weekday = None
        else:
            weekday = int(dow_p)
            if not (0 <= weekday <= 6):
                return None

        return ParsedSchedule(minute=minute, hour=hour, weekday=weekday, raw_expr=raw_expr)
    except (ValueError, IndexError):
        return None


# ── State persistence ──────────────────────────────────────────────────────────

class SchedulerState:
    """Atomic-write JSON persistence for scheduled tasks."""

    def __init__(self, entries: dict[str, dict] | None = None) -> None:
        # id → {"room_id", "expr", "task", "created_by", "created_at", "last_fired"}
        self.entries: dict[str, dict] = entries or {}

    @classmethod
    def load(cls, path: str) -> "SchedulerState":
        file = Path(path)
        if not file.exists():
            return cls()
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
            return cls(entries={k: dict(v) for k, v in payload.get("entries", {}).items()})
        except Exception:
            log.exception("failed to load schedules from %s, starting empty", path)
            return cls()

    def save(self, path: str) -> None:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"entries": self.entries}, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        tmp.replace(file)


# ── Runner ─────────────────────────────────────────────────────────────────────

class ScheduledTaskRunner:
    """Background daemon thread: checks every 30 s, fires tasks on time."""

    def __init__(
        self,
        *,
        state_file: str,
        fire_fn: Callable[[str, str, str], None],
        # fire_fn(schedule_id, room_id, task_text)
    ) -> None:
        self._state_file = state_file
        self._fire_fn    = fire_fn
        self._state      = SchedulerState.load(state_file)
        self._lock       = threading.Lock()
        self._stop_ev    = threading.Event()
        self._thread     = threading.Thread(
            target=self._run, daemon=True, name="devagent-scheduler"
        )
        log.info("scheduler loaded %d schedule(s) from %s", len(self._state.entries), state_file)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_ev.set()

    def add(self, *, room_id: str, expr: str, task: str, created_by: str) -> tuple[str, ParsedSchedule] | None:
        """Parse expr and add schedule.  Returns (schedule_id, parsed) or None on parse error."""
        parsed = parse_schedule_expr(expr)
        if parsed is None:
            return None
        sched_id = uuid.uuid4().hex[:8]
        entry: dict = {
            "room_id":    room_id,
            "expr":       parsed.raw_expr,
            "task":       task,
            "created_by": created_by,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "last_fired": None,
        }
        with self._lock:
            self._state.entries[sched_id] = entry
            self._state.save(self._state_file)
        log.info("schedule %s added by %s: [%s] %s", sched_id, created_by, expr, task[:60])
        return sched_id, parsed

    def remove(self, sched_id: str) -> bool:
        """Remove a schedule by ID.  Returns True if found and removed."""
        with self._lock:
            if sched_id not in self._state.entries:
                return False
            del self._state.entries[sched_id]
            self._state.save(self._state_file)
        log.info("schedule %s removed", sched_id)
        return True

    def list_for_room(self, room_id: str) -> list[dict]:
        """Return all schedule entries for a room, sorted by created_at."""
        with self._lock:
            entries = [
                {"id": k, **v}
                for k, v in self._state.entries.items()
                if v.get("room_id") == room_id
            ]
        return sorted(entries, key=lambda e: e.get("created_at", ""))

    def list_all(self) -> list[dict]:
        with self._lock:
            return [{"id": k, **v} for k, v in self._state.entries.items()]

    # ── Background loop ───────────────────────────────────────────────────────

    def _run(self) -> None:
        log.debug("scheduler thread started")
        while not self._stop_ev.wait(30):   # wake every 30 s
            try:
                self._tick(datetime.now())
            except Exception:
                log.exception("scheduler tick error")

    def _tick(self, now: datetime) -> None:
        with self._lock:
            entries = list(self._state.entries.items())

        for sched_id, entry in entries:
            parsed = parse_schedule_expr(entry.get("expr", ""))
            if parsed is None:
                continue
            if not parsed.matches(now):
                continue
            # Avoid double-firing within the same minute
            last = entry.get("last_fired")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.replace(second=0, microsecond=0) == now.replace(second=0, microsecond=0):
                        continue
                except ValueError:
                    pass

            log.info("firing schedule %s: %s", sched_id, entry.get("task", "")[:60])
            try:
                self._fire_fn(sched_id, entry["room_id"], entry["task"])
            except Exception:
                log.exception("fire_fn failed for schedule %s", sched_id)

            with self._lock:
                if sched_id in self._state.entries:
                    self._state.entries[sched_id]["last_fired"] = now.isoformat(timespec="seconds")
                    self._state.save(self._state_file)
