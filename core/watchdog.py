"""Background watchdog: detects orphaned/hung tmux jobs and force-fails them."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

_DEFAULT_CHECK_INTERVAL = 30      # seconds between health checks
_DEFAULT_MAX_JOB_SECONDS = 7200   # 2-hour hard cap per running job
_DEFAULT_MAX_WAIT_SECONDS = 3600  # 1-hour cap for unanswered approval requests


class JobWatchdog:
    """Periodically checks running jobs for dead sessions or exceeded time limits.

    Requires:
      - engine:          DevAgentEngine  (for running_jobs / fail_job)
      - tmux:            TmuxDriver      (for session_exists / stop_session)
      - room_id_for:     Callable[[str], str | None]  maps job_id → Matrix room_id
      - notify_fn:       Callable[[str, str], None]   sends a notice to a Matrix room
    """

    def __init__(
        self,
        engine,
        tmux,
        room_id_for: Callable[[str], str | None],
        notify_fn: Callable[[str, str], None],
        check_interval: int = _DEFAULT_CHECK_INTERVAL,
        max_job_seconds: int = _DEFAULT_MAX_JOB_SECONDS,
        max_wait_seconds: int = _DEFAULT_MAX_WAIT_SECONDS,
    ) -> None:
        self._engine = engine
        self._tmux = tmux
        self._room_id_for = room_id_for
        self._notify = notify_fn
        self._interval = check_interval
        self._max_seconds = max_job_seconds
        self._max_wait_seconds = max_wait_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="job-watchdog"
        )

    def start(self) -> None:
        self._thread.start()
        log.info(
            "job watchdog started (interval=%ds, max_job=%ds, max_wait=%ds)",
            self._interval, self._max_seconds, self._max_wait_seconds,
        )

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._check_once()
            except Exception:
                log.exception("watchdog check raised an unexpected error")

    def _check_once(self) -> None:
        for job in self._engine.running_jobs():
            try:
                self._check_job(job)
            except Exception:
                log.exception("watchdog: error checking job %s", job.job_id)
        for job in self._engine.waiting_jobs():
            try:
                self._check_waiting_job(job)
            except Exception:
                log.exception("watchdog: error checking waiting job %s", job.job_id)

    def _check_job(self, job) -> None:
        # --- Check 1: tmux session still alive? ---
        if not self._tmux.session_exists(job_id=job.job_id):
            log.warning(
                "watchdog: tmux session gone for job %s — force-failing", job.job_id
            )
            self._engine.fail_job(job.job_id)
            self._send(job.job_id, f"⚠️ Job `{job.job_id}` ist unerwartet beendet (Session verloren).")
            return

        # --- Check 2: hard time limit exceeded? ---
        if job.started_at and (time.time() - job.started_at) > self._max_seconds:
            hours = self._max_seconds // 3600
            log.warning(
                "watchdog: job %s exceeded %dh limit — stopping", job.job_id, hours
            )
            self._tmux.stop_session(job_id=job.job_id)
            self._engine.fail_job(job.job_id)
            self._send(job.job_id, f"⏱ Job `{job.job_id}` nach {hours}h Laufzeit abgebrochen.")

    def _check_waiting_job(self, job) -> None:
        if not job.wait_approval_at:
            return
        age = time.time() - job.wait_approval_at
        if age > self._max_wait_seconds:
            hours = self._max_wait_seconds // 3600
            log.warning(
                "watchdog: job %s waiting for approval for >%dh — cancelling", job.job_id, hours
            )
            self._engine.fail_job(job.job_id)
            self._send(
                job.job_id,
                f"⏱ Job `{job.job_id}` nach {hours}h ohne Reaktion abgebrochen.",
            )

    def _send(self, job_id: str, message: str) -> None:
        room_id = self._room_id_for(job_id)
        if room_id:
            try:
                self._notify(room_id, message)
            except Exception:
                log.exception("watchdog: failed to send notice for job %s", job_id)
