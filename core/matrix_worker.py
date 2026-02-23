"""Matrix live worker: polls room events and drives DevAgent workflow."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.matrix.ai_handler import parse_ai_message, run_ai_task
from adapters.matrix.client import MatrixApiError, MatrixClient
from adapters.matrix.listener import MatrixListenerConfig, MatrixRoomListener
from core.engine import DevAgentEngine
from core.job_service import JobService
from core.models import JobState
from core.security import parse_allowed_users
from core.watchdog import JobWatchdog
from core.worktree_manager import WorktreeManager
from runner.job_runner import JobRunner

log = logging.getLogger("devagent.worker")


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MatrixWorkerConfig:
    homeserver_url: str
    access_token: str
    # Primary room (backward-compat); additional rooms come from projects_file
    room_id: str
    allowed_users: set[str]
    state_file: str
    artifacts_root: str
    projects_file: str = "/srv/devagent/state/projects.json"
    poll_timeout_ms: int = 30000
    retry_sleep_seconds: float = 2.0
    send_notices: bool = True
    repos_root: str = "/srv/repos"
    claude_bin: str = "claude"
    ai_timeout_seconds: int = 120
    max_job_seconds: int = 7200
    relogin_user: str = ""
    relogin_password: str = ""
    relogin_env_file: str = "/srv/devagent/.env"


# ── State ─────────────────────────────────────────────────────────────────────

class MatrixWorkerState:
    def __init__(
        self,
        since: str | None = None,
        jobcards: dict[str, dict[str, str]] | None = None,
        job_states: dict[str, str] | None = None,
    ) -> None:
        self.since = since
        self.jobcards = jobcards or {}
        self.job_states = job_states or {}

    @classmethod
    def load(cls, path: str) -> "MatrixWorkerState":
        file = Path(path)
        if not file.exists():
            return cls()
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
            return cls(
                since=payload.get("since"),
                jobcards={k: dict(v) for k, v in payload.get("jobcards", {}).items()},
                job_states={k: str(v) for k, v in payload.get("job_states", {}).items()},
            )
        except Exception:
            log.exception("failed to load state from %s, starting fresh", path)
            return cls()

    def save(self, path: str) -> None:
        """Atomic write: write to .tmp then replace."""
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"since": self.since, "jobcards": self.jobcards, "job_states": self.job_states},
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        tmp.replace(file)


# ── Worker ────────────────────────────────────────────────────────────────────

class MatrixWorker:
    def __init__(
        self,
        *,
        config: MatrixWorkerConfig,
        client: MatrixClient,
        engine: DevAgentEngine,
        jobs: JobService,
        worktrees: WorktreeManager,
    ) -> None:
        self.config = config
        self.client = client
        self.engine = engine
        self.jobs = jobs
        self.worktrees = worktrees
        self.listener = MatrixRoomListener(
            MatrixListenerConfig(room_id=config.room_id, allowed_senders=config.allowed_users)
        )
        self.state = MatrixWorkerState.load(config.state_file)
        self._restore_engine_jobs()
        self._running = True
        self._ai_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ai-task")
        # Cache of room_id → project_name, refreshed each cycle
        self._room_map: dict[str, str] = {}
        self._sync_count = 0
        # Rolling conversation history per room: deque of {sender_name, body}
        self._room_history: dict[str, deque] = {}
        self._history_size = int(os.getenv("DEVAGENT_HISTORY_SIZE", "20"))
        # Per-room lock: only one active AI task per room at a time
        self._room_locks: dict[str, threading.Lock] = {}
        self._room_locks_mutex = threading.Lock()
        # Per-room cancel event for !cancel command
        self._room_cancel: dict[str, threading.Event] = {}
        # Per-room task start time for !status
        self._room_task_start: dict[str, float] = {}
        self._worker_start = time.time()

        # Background watchdog for orphaned/hung tmux jobs
        _tmux = getattr(engine.runner, "tmux", None) if engine.runner is not None else None
        if _tmux is not None:
            self._watchdog: JobWatchdog | None = JobWatchdog(
                engine=engine,
                tmux=_tmux,
                room_id_for=self._room_id_for_job,
                notify_fn=lambda room_id, msg: self.client.send_notice(room_id=room_id, body=msg),
                max_job_seconds=config.max_job_seconds,
            )
            self._watchdog.start()
        else:
            self._watchdog = None

    def _room_id_for_job(self, job_id: str) -> str | None:
        """Look up the Matrix room that owns a given job_id."""
        for context in self.state.jobcards.values():
            if context.get("job_id") == job_id:
                return context.get("room_id") or None
        return None

    def stop(self) -> None:
        self._running = False
        self._ai_executor.shutdown(wait=False)
        if self._watchdog is not None:
            self._watchdog.stop()

    # ── Room map (projects.json → room_id → project_name) ────────────────────

    def _refresh_room_map(self) -> None:
        """Read projects.json and build room_id → project_name mapping."""
        try:
            path = Path(self.config.projects_file)
            if not path.exists():
                self._room_map = {}
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            new_map: dict[str, str] = {}
            for name, proj in data.get("projects", {}).items():
                rid = proj.get("matrix_room_id", "")
                if rid:
                    new_map[rid] = name
            if new_map != self._room_map:
                added = set(new_map) - set(self._room_map)
                removed = set(self._room_map) - set(new_map)
                if added:
                    log.info("watching new rooms: %s", added)
                if removed:
                    log.info("stopped watching rooms: %s", removed)
                self._room_map = new_map
        except Exception:
            log.exception("failed to refresh room map from %s", self.config.projects_file)

    def _active_rooms(self) -> set[str]:
        """All rooms to listen to: primary + projects."""
        rooms = set(self._room_map.keys())
        if self.config.room_id:
            rooms.add(self.config.room_id)
        return rooms

    def _room_id_for_event(self, event: dict[str, Any], fallback_room_id: str) -> str:
        return str(event.get("room_id") or fallback_room_id)

    def _project_for_room(self, room_id: str) -> dict[str, Any] | None:
        """Return project data dict from projects.json for the given room_id."""
        name = self._room_map.get(room_id)
        if not name:
            return None
        try:
            path = Path(self.config.projects_file)
            data = json.loads(path.read_text(encoding="utf-8"))
            proj = data.get("projects", {}).get(name)
            if proj:
                proj["name"] = name
            return proj
        except Exception:
            return None

    # ── Status file (for UI health indicator) ────────────────────────────────

    def _write_status(self) -> None:
        try:
            status_path = Path(self.config.state_file).parent / "worker_status.json"
            tmp = status_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "rooms_watched": sorted(self._active_rooms()),
                    "active_jobs": len(self.engine.jobs),
                    "since": (self.state.since or "")[:40],
                }, indent=2),
                encoding="utf-8",
            )
            tmp.replace(status_path)
        except Exception:
            pass  # status is best-effort

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _warn_if_no_relogin(self) -> None:
        """Send a one-time warning to the primary room if auto-relogin is not configured."""
        if self.client._login_user:  # noqa: SLF001
            return
        msg = (
            "⚠️ **DevAgent Warnung:** Auto-Relogin ist nicht konfiguriert.\n"
            "Wenn der Matrix-Access-Token abläuft, stoppt der Bot ohne Vorwarnung.\n"
            "Bitte `MATRIX_USER_DEVAGENT` und `MATRIX_PASSWORD_DEVAGENT` in `.env` setzen."
        )
        try:
            self.client.send_message(room_id=self.config.room_id, body=msg)
        except Exception:
            log.debug("could not send relogin warning to room (non-fatal)")

    def run_forever(self) -> None:
        log.info("worker started, primary room=%s", self.config.room_id)
        self._warn_if_no_relogin()
        while self._running:
            # Refresh room map every cycle (cheap JSON read)
            self._refresh_room_map()
            try:
                sync = self.client.sync(since=self.state.since, timeout_ms=self.config.poll_timeout_ms)
                self.process_sync_payload(sync.payload)
                self.state.since = sync.next_batch
                self.state.save(self.config.state_file)
                self._sync_count += 1
                if self._sync_count % 10 == 0:
                    self._write_status()
                    log.debug("sync cycle %d, watching %d room(s)", self._sync_count, len(self._active_rooms()))
            except MatrixApiError as exc:
                log.error("matrix sync error: %s", exc)
                time.sleep(self.config.retry_sleep_seconds)
            except Exception:
                log.exception("unhandled error in sync cycle")
                time.sleep(self.config.retry_sleep_seconds)

    # ── Event routing ─────────────────────────────────────────────────────────

    def process_sync_payload(self, payload: dict[str, Any]) -> None:
        join = payload.get("rooms", {}).get("join", {})
        active = self._active_rooms()
        for room_id, room_data in join.items():
            if room_id not in active:
                continue
            if not isinstance(room_data, dict):
                continue
            events = room_data.get("timeline", {}).get("events", [])
            for event in events:
                if isinstance(event, dict):
                    event = dict(event)
                    event["room_id"] = room_id  # inject room context
                    self.process_event(event)

    def _record_history(self, event: dict[str, Any]) -> None:
        """Store a m.room.message in the per-room rolling buffer."""
        content = event.get("content", {})
        body = content.get("body", "") if isinstance(content, dict) else ""
        if not body or body.startswith("🤔 Arbeite"):  # skip "working" notices
            return
        room_id = str(event.get("room_id", ""))
        sender  = str(event.get("sender", ""))
        name    = sender.split(":")[0].lstrip("@")  # localpart only
        if room_id not in self._room_history:
            self._room_history[room_id] = deque(maxlen=self._history_size)
        self._room_history[room_id].append({"name": name, "body": body[:600]})

    def process_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "devagent.jobcard":
            self._handle_jobcard(event)
        elif event_type == "m.room.message":
            self._record_history(event)
            body = (event.get("content") or {}).get("body", "").strip()
            lower = body.lower()
            sender = str(event.get("sender", ""))
            if sender not in self.config.allowed_users:
                return
            if lower == "!status":
                self._handle_status(event)
            elif lower == "!cancel":
                self._handle_cancel(event)
            elif lower == "!help":
                self._handle_help(event)
            elif self._is_ai_message(event):
                self._handle_ai_message(event)
            elif lower.startswith("devagent_jobcard "):
                self._handle_message_jobcard(event)
            elif not lower.startswith("!"):
                # Plain message — not a command, send short hint
                room_id = str(event.get("room_id", self.config.room_id))
                self.client.send_notice(
                    room_id=room_id,
                    body="ℹ️ Nachricht nicht verarbeitet. Benutze `!ai <aufgabe>` oder `!help`.",
                )
            # messages starting with unknown ! are silently ignored
        elif event_type == "m.reaction":
            self._handle_reaction(event)

    # ── Job helpers ───────────────────────────────────────────────────────────

    def _restore_engine_jobs(self) -> None:
        for job_id, state_raw in self.state.job_states.items():
            if job_id in self.engine.jobs:
                continue
            record = self.engine.create_job(job_id)
            try:
                record.state = JobState(state_raw)
            except ValueError:
                record.state = JobState.WAIT_APPROVAL

    def _ensure_job_exists(self, job_id: str) -> None:
        if job_id in self.engine.jobs:
            return
        state_raw = self.state.job_states.get(job_id, JobState.WAIT_APPROVAL.value)
        record = self.engine.create_job(job_id)
        try:
            record.state = JobState(state_raw)
        except ValueError:
            record.state = JobState.WAIT_APPROVAL

    # ── Jobcard handlers ──────────────────────────────────────────────────────

    def _handle_jobcard(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id", ""))
        room_id = str(event.get("room_id", self.config.room_id))
        if not event_id or event_id in self.state.jobcards:
            return

        card = self.listener.extract_job_request(event)
        if card is None:
            return

        prepared = self.jobs.create_from_jobcard(card)
        self.state.jobcards[event_id] = {
            "job_id": card.job_id,
            "repo": card.repo,
            "branch": card.branch,
            "command": card.command,
            "requested_by": card.requested_by,
            "worktree_path": prepared.worktree_path,
            "room_id": room_id,
        }
        self.state.job_states[card.job_id] = self.engine.get_job(card.job_id).state.value
        log.info("job %s created from %s (room %s)", card.job_id, card.requested_by, room_id)

        if self.config.send_notices:
            self.client.send_notice(room_id=room_id, body=f"job {card.job_id} created; waiting for approval (✅ / ❌)")

    def _handle_message_jobcard(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id", ""))
        room_id = str(event.get("room_id", self.config.room_id))
        if not event_id or event_id in self.state.jobcards:
            return

        sender = str(event.get("sender", ""))
        if sender not in self.config.allowed_users:
            return

        content = event.get("content", {})
        if not isinstance(content, dict):
            return
        body = content.get("body")
        if not isinstance(body, str):
            return
        body = body.strip()
        if not body.startswith("DEVAGENT_JOBCARD "):
            return

        try:
            job_payload = json.loads(body[len("DEVAGENT_JOBCARD "):])
        except json.JSONDecodeError:
            log.warning("invalid DEVAGENT_JOBCARD JSON from %s", sender)
            return

        synthetic = {
            "event_id": event_id,
            "sender": sender,
            "type": "devagent.jobcard",
            "room_id": room_id,
            "content": job_payload,
        }
        self._handle_jobcard(synthetic)

    def _handle_reaction(self, event: dict[str, Any]) -> None:
        content = event.get("content", {})
        relates = content.get("m.relates_to", {}) if isinstance(content, dict) else {}
        target_event_id = str(relates.get("event_id", ""))
        reaction = str(relates.get("key", ""))
        event_id = str(event.get("event_id", ""))
        sender = str(event.get("sender", ""))
        room_id = str(event.get("room_id", self.config.room_id))

        if not target_event_id or not reaction or not sender:
            return

        context = self.state.jobcards.get(target_event_id)
        if context is None:
            return

        # Use the room the jobcard was originally posted in for notices
        notice_room = context.get("room_id", room_id)
        self._ensure_job_exists(context["job_id"])

        normalized_reaction = reaction.replace("\ufe0f", "").strip()
        run_command = context["command"] if normalized_reaction == "✅" else None
        run_cwd = context["worktree_path"] if normalized_reaction == "✅" else None

        decision = self.engine.handle_matrix_reaction(
            job_id=context["job_id"],
            reaction=reaction,
            user_id=sender,
            allowed_users=self.config.allowed_users,
            action_id=event_id or None,
            run_command=run_command,
            run_cwd=run_cwd,
        )

        if decision.accepted and normalized_reaction in {"❌", "🛑"}:
            try:
                self.worktrees.cleanup(context["repo"], context["job_id"])
            except Exception:
                log.exception("cleanup failed for job %s", context["job_id"])

        self.state.job_states[context["job_id"]] = self.engine.get_job(context["job_id"]).state.value
        log.info("reaction %s by %s on job %s: accepted=%s", reaction, sender, context["job_id"], decision.accepted)

        if self.config.send_notices:
            state = self.engine.get_job(context["job_id"]).state.value
            self.client.send_notice(
                room_id=notice_room,
                body=(
                    f"reaction {reaction} by {sender}: accepted={decision.accepted} "
                    f"job={context['job_id']} state={state} reason={decision.reason}"
                ),
            )

    # ── Control commands ──────────────────────────────────────────────────────

    def _handle_status(self, event: dict[str, Any]) -> None:
        room_id = str(event.get("room_id", self.config.room_id))
        uptime  = int(time.time() - self._worker_start)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

        with self._room_locks_mutex:
            lock = self._room_locks.get(room_id)
            busy = lock is not None and lock.locked()

        if busy:
            started = self._room_task_start.get(room_id)
            running_s = int(time.time() - started) if started else "?"
            task_line = f"🔄 Task läuft seit {running_s}s — `!cancel` zum Abbrechen"
        else:
            task_line = "✅ Kein Task aktiv"

        rooms = len(self._active_rooms())
        self.client.send_notice(
            room_id=room_id,
            body=(
                f"**DevAgent Status**\n"
                f"Uptime: {h}h {m}m {s}s\n"
                f"Überwachte Räume: {rooms}\n"
                f"Sync-Zyklen: {self._sync_count}\n"
                f"{task_line}"
            ),
        )

    def _handle_cancel(self, event: dict[str, Any]) -> None:
        room_id = str(event.get("room_id", self.config.room_id))
        # Use the lock as the authoritative signal for "is a task running?"
        with self._room_locks_mutex:
            lock = self._room_locks.get(room_id)
            busy = lock is not None and lock.locked()
        if not busy:
            self.client.send_notice(room_id=room_id, body="ℹ️ Kein Task aktiv.")
            return
        cancel_event = self._room_cancel.get(room_id)
        if cancel_event:
            cancel_event.set()
            self.client.send_notice(room_id=room_id, body="🚫 Abbruch angefordert…")
        else:
            self.client.send_notice(room_id=room_id, body="ℹ️ Kein Task aktiv.")

    def _handle_help(self, event: dict[str, Any]) -> None:
        room_id = str(event.get("room_id", self.config.room_id))
        self.client.send_notice(
            room_id=room_id,
            body=(
                "**DevAgent Befehle**\n"
                "`!ai <aufgabe>` — Claude Code ausführen\n"
                "`!ai @<repo> <aufgabe>` — In spezifischem Repo ausführen\n"
                "`!status` — Worker-Status anzeigen\n"
                "`!cancel` — Laufenden Task abbrechen\n"
                "`!help` — Diese Hilfe"
            ),
        )

    # ── Output splitting ──────────────────────────────────────────────────────

    @staticmethod
    def _split_for_matrix(text: str, max_chars: int = 3800) -> list[str]:
        """Split text at paragraph boundaries into chunks of at most max_chars."""
        if len(text) <= max_chars:
            return [text]
        chunks: list[str] = []
        buf = ""
        for para in text.split("\n\n"):
            candidate = (buf + "\n\n" + para) if buf else para
            if len(candidate) <= max_chars:
                buf = candidate
            else:
                if buf:
                    chunks.append(buf)
                if len(para) <= max_chars:
                    buf = para
                else:
                    # para too long — hard-cut at max_chars
                    while len(para) > max_chars:
                        chunks.append(para[:max_chars])
                        para = para[max_chars:]
                    buf = para
        if buf:
            chunks.append(buf)
        return chunks

    # ── AI message handlers ───────────────────────────────────────────────────

    def _is_ai_message(self, event: dict[str, Any]) -> bool:
        sender = str(event.get("sender", ""))
        if sender not in self.config.allowed_users:
            return False
        content = event.get("content", {})
        body = content.get("body", "") if isinstance(content, dict) else ""
        return isinstance(body, str) and body.strip().lower().startswith("!ai ")

    def _handle_ai_message(self, event: dict[str, Any]) -> None:
        sender = str(event.get("sender", ""))
        room_id = str(event.get("room_id", self.config.room_id))
        content = event.get("content", {})
        body = str(content.get("body", "")).strip() if isinstance(content, dict) else ""

        parsed = parse_ai_message(body)
        if parsed is None:
            return

        repo_hint, task = parsed

        # Resolve working directory
        if repo_hint:
            # Explicit @repo prefix
            cwd = str(Path(self.config.repos_root) / repo_hint)
            context_hint = f"@{repo_hint}"
            if not Path(cwd).is_dir():
                self.client.send_notice(
                    room_id=room_id,
                    body=f"❌ Repo '{repo_hint}' nicht gefunden unter {self.config.repos_root}",
                )
                return
        else:
            # Infer from the room's project
            proj = self._project_for_room(room_id)
            if proj and proj.get("local_path") and Path(proj["local_path"]).is_dir():
                cwd = proj["local_path"]
                context_hint = proj["name"]
            elif Path(self.config.repos_root).is_dir():
                cwd = self.config.repos_root
                context_hint = "kein Repo-Kontext"
            else:
                cwd = str(Path.home())
                context_hint = "kein Repo-Kontext"

        # Check per-room lock — reject if a task is already running in this room
        with self._room_locks_mutex:
            if room_id not in self._room_locks:
                self._room_locks[room_id] = threading.Lock()
            room_lock = self._room_locks[room_id]

        if not room_lock.acquire(blocking=False):
            self.client.send_notice(
                room_id=room_id,
                body="⏳ Eine Aufgabe läuft bereits in diesem Raum. Bitte warten.",
            )
            log.info("ai task queued/rejected for %s — room %s busy", sender, room_id)
            return

        # Fresh cancel event for this task
        cancel_event = threading.Event()
        self._room_cancel[room_id] = cancel_event
        self._room_task_start[room_id] = time.time()

        self.client.send_notice(room_id=room_id, body=f"🤔 Arbeite daran [{context_hint}]…")
        log.info("ai task started by %s in %s (cwd=%s): %s", sender, context_hint, cwd, task[:120])

        # Snapshot history (exclude the current !ai message — last entry)
        history = list(self._room_history.get(room_id, []))[:-1]

        # Run non-blocking in thread pool (lock released inside _run_ai_task_async)
        self._ai_executor.submit(
            self._run_ai_task_async, task, cwd, context_hint, room_id, sender, history,
            room_lock, cancel_event,
        )

    def _run_ai_task_async(
        self, task: str, cwd: str, context_hint: str, room_id: str, sender: str,
        history: list[dict] | None = None,
        room_lock: threading.Lock | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Executed in thread pool — must not touch self.state directly."""
        try:
            if history:
                lines = "\n".join(f"{h['name']}: {h['body']}" for h in history)
                message = (
                    f"## Gesprächsverlauf in diesem Raum\n{lines}\n\n"
                    f"## Aktuelle Aufgabe\n{task}"
                )
            else:
                message = task
            result = run_ai_task(
                message=message,
                cwd=cwd,
                claude_bin=self.config.claude_bin,
                timeout_seconds=self.config.ai_timeout_seconds,
                cancel_event=cancel_event,
            )
            status = "✅" if result.success else "❌"
            full_output = result.output
            chunks = self._split_for_matrix(full_output)
            try:
                for i, chunk in enumerate(chunks):
                    prefix = f"{status} " if i == 0 else ""
                    suffix = f"\n\n[Teil {i+1}/{len(chunks)}]" if len(chunks) > 1 else ""
                    self.client.send_notice(room_id=room_id, body=f"{prefix}{chunk}{suffix}")
                if result.truncated:
                    self.client.send_notice(room_id=room_id, body="⚠️ Ausgabe wurde intern gekürzt (>64 KB).")
            except Exception:
                log.exception("failed to send ai result to room %s", room_id)
            log.info("ai task done by %s [%s]: exit=%d, output_len=%d", sender, context_hint, result.exit_code, len(result.output))
        finally:
            if room_lock is not None:
                room_lock.release()


# ── Config loading ────────────────────────────────────────────────────────────

def load_config_from_env() -> MatrixWorkerConfig:
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "").strip()
    access_token   = os.getenv("MATRIX_ACCESS_TOKEN", "").strip()
    room_id        = os.getenv("MATRIX_ROOM_ID", "").strip()
    allowed_users  = parse_allowed_users(os.getenv("DEVAGENT_ALLOWED_USERS", ""))

    missing: list[str] = []
    if not homeserver_url:
        missing.append("MATRIX_HOMESERVER_URL")
    if not access_token:
        missing.append("MATRIX_ACCESS_TOKEN")
    if not allowed_users:
        missing.append("DEVAGENT_ALLOWED_USERS")
    if missing:
        raise ValueError(f"missing required env values: {', '.join(missing)}")

    return MatrixWorkerConfig(
        homeserver_url=homeserver_url,
        access_token=access_token,
        room_id=room_id,  # optional now; additional rooms from projects_file
        allowed_users=allowed_users,
        state_file=os.getenv("DEVAGENT_MATRIX_STATE_FILE", "/srv/devagent/state/matrix_worker_state.json"),
        artifacts_root=os.getenv("DEVAGENT_ARTIFACTS_ROOT", "/srv/agent-artifacts"),
        projects_file=os.getenv("DEVAGENT_PROJECTS_FILE", "/srv/devagent/state/projects.json"),
        poll_timeout_ms=int(os.getenv("DEVAGENT_MATRIX_SYNC_TIMEOUT_MS", "30000")),
        retry_sleep_seconds=float(os.getenv("DEVAGENT_MATRIX_RETRY_SLEEP_SECONDS", "2.0")),
        send_notices=os.getenv("DEVAGENT_MATRIX_SEND_NOTICES", "1") not in {"0", "false", "False"},
        repos_root=os.getenv("DEVAGENT_REPOS_ROOT", "/srv/repos"),
        claude_bin=os.getenv("DEVAGENT_CLAUDE_BIN", "claude"),
        ai_timeout_seconds=int(os.getenv("DEVAGENT_AI_TIMEOUT_SECONDS", "120")),
        relogin_user=os.getenv("MATRIX_USER_DEVAGENT", ""),
        relogin_password=os.getenv("MATRIX_PASSWORD_DEVAGENT", ""),
        relogin_env_file=os.getenv("DEVAGENT_ENV_FILE", "/srv/devagent/.env"),
    )


def build_worker(config: MatrixWorkerConfig) -> MatrixWorker:
    scripts_dir = str(Path(__file__).parent.parent / "scripts")
    runner   = JobRunner()
    engine   = DevAgentEngine(artifacts_root=config.artifacts_root, runner=runner)
    worktrees = WorktreeManager(scripts_dir=scripts_dir)
    jobs     = JobService(engine=engine, worktrees=worktrees)
    client = MatrixClient(config.homeserver_url, config.access_token)
    if config.relogin_user and config.relogin_password:
        client.set_relogin_credentials(
            user=config.relogin_user,
            password=config.relogin_password,
            env_file=config.relogin_env_file,
        )
        log.info("auto re-login configured for %s", config.relogin_user)
    else:
        log.warning(
            "⚠️  Auto-relogin NOT configured — if the Matrix access token expires the worker "
            "will stop responding. Set MATRIX_USER_DEVAGENT and MATRIX_PASSWORD_DEVAGENT in .env "
            "to enable automatic token renewal."
        )
    return MatrixWorker(config=config, client=client, engine=engine, jobs=jobs, worktrees=worktrees)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("DEVAGENT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    parser = argparse.ArgumentParser(description="DevAgent Matrix live worker")
    parser.add_argument("--once", action="store_true", help="process one sync cycle and exit")
    args = parser.parse_args()

    config = load_config_from_env()
    worker = build_worker(config)

    def _signal_handler(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        worker.stop()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    if args.once:
        worker._refresh_room_map()
        sync = worker.client.sync(since=worker.state.since, timeout_ms=config.poll_timeout_ms)
        worker.process_sync_payload(sync.payload)
        worker.state.since = sync.next_batch
        worker.state.save(config.state_file)
        return

    worker.run_forever()


if __name__ == "__main__":
    main()
