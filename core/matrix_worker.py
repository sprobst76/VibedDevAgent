"""Matrix live worker: polls room events and drives DevAgent workflow."""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.matrix.client import MatrixApiError, MatrixClient
from adapters.matrix.listener import MatrixListenerConfig, MatrixRoomListener
from core.engine import DevAgentEngine
from core.job_service import JobService
from core.models import JobState
from core.security import parse_allowed_users
from core.worktree_manager import WorktreeManager
from runner.job_runner import JobRunner


@dataclass(frozen=True)
class MatrixWorkerConfig:
    homeserver_url: str
    access_token: str
    room_id: str
    allowed_users: set[str]
    state_file: str
    artifacts_root: str
    poll_timeout_ms: int = 30000
    retry_sleep_seconds: float = 2.0
    send_notices: bool = True


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

        payload = json.loads(file.read_text(encoding="utf-8"))
        return cls(
            since=payload.get("since"),
            jobcards={k: dict(v) for k, v in payload.get("jobcards", {}).items()},
            job_states={k: str(v) for k, v in payload.get("job_states", {}).items()},
        )

    def save(self, path: str) -> None:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(
                {"since": self.since, "jobcards": self.jobcards, "job_states": self.job_states},
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )


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

    def stop(self) -> None:
        self._running = False

    def run_forever(self) -> None:
        while self._running:
            try:
                sync = self.client.sync(since=self.state.since, timeout_ms=self.config.poll_timeout_ms)
                self.process_sync_payload(sync.payload)
                self.state.since = sync.next_batch
                self.state.save(self.config.state_file)
            except MatrixApiError as exc:
                print(f"matrix sync error: {exc}")
                time.sleep(self.config.retry_sleep_seconds)
            except Exception as exc:  # noqa: BLE001
                print(f"matrix worker error: {exc}")
                time.sleep(self.config.retry_sleep_seconds)

    def process_sync_payload(self, payload: dict[str, Any]) -> None:
        join = payload.get("rooms", {}).get("join", {})
        room = join.get(self.config.room_id)
        if not isinstance(room, dict):
            return

        timeline = room.get("timeline", {})
        events = timeline.get("events", [])
        if not isinstance(events, list):
            return

        for event in events:
            if not isinstance(event, dict):
                continue
            self.process_event(event)

    def process_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "devagent.jobcard":
            self._handle_jobcard(event)
            return

        if event_type == "m.room.message":
            self._handle_message_jobcard(event)
            return

        if event_type == "m.reaction":
            self._handle_reaction(event)

    def _event_with_room(self, event: dict[str, Any]) -> dict[str, Any]:
        data = dict(event)
        data["room_id"] = self.config.room_id
        return data

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

    def _handle_jobcard(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id", ""))
        if not event_id or event_id in self.state.jobcards:
            return

        card = self.listener.extract_job_request(self._event_with_room(event))
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
        }
        self.state.job_states[card.job_id] = self.engine.get_job(card.job_id).state.value

        if self.config.send_notices:
            self.client.send_notice(room_id=self.config.room_id, body=f"job {card.job_id} created; waiting for approval")

    def _handle_message_jobcard(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id", ""))
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

        prefix = "DEVAGENT_JOBCARD "
        if not body.startswith(prefix):
            return

        try:
            job_payload = json.loads(body[len(prefix) :])
        except json.JSONDecodeError:
            return

        synthetic = {
            "event_id": event_id,
            "sender": sender,
            "type": "devagent.jobcard",
            "room_id": self.config.room_id,
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
        if not target_event_id or not reaction or not sender:
            return

        context = self.state.jobcards.get(target_event_id)
        if context is None:
            return
        self._ensure_job_exists(context["job_id"])

        run_command = context["command"] if reaction == "✅" else None
        run_cwd = context["worktree_path"] if reaction == "✅" else None

        decision = self.engine.handle_matrix_reaction(
            job_id=context["job_id"],
            reaction=reaction,
            user_id=sender,
            allowed_users=self.config.allowed_users,
            action_id=event_id or None,
            run_command=run_command,
            run_cwd=run_cwd,
        )

        if decision.accepted and reaction in {"❌", "🛑"}:
            try:
                self.worktrees.cleanup(context["repo"], context["job_id"])
            except Exception as exc:  # noqa: BLE001
                print(f"cleanup failed for job {context['job_id']}: {exc}")

        self.state.job_states[context["job_id"]] = self.engine.get_job(context["job_id"]).state.value

        if self.config.send_notices:
            state = self.engine.get_job(context["job_id"]).state.value
            self.client.send_notice(
                room_id=self.config.room_id,
                body=(
                    f"reaction {reaction} by {sender}: accepted={decision.accepted} "
                    f"job={context['job_id']} state={state} reason={decision.reason}"
                ),
            )


def load_config_from_env() -> MatrixWorkerConfig:
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "").strip()
    access_token = os.getenv("MATRIX_ACCESS_TOKEN", "").strip()
    room_id = os.getenv("MATRIX_ROOM_ID", "").strip()
    allowed_users = parse_allowed_users(os.getenv("DEVAGENT_ALLOWED_USERS", ""))

    missing: list[str] = []
    if not homeserver_url:
        missing.append("MATRIX_HOMESERVER_URL")
    if not access_token:
        missing.append("MATRIX_ACCESS_TOKEN")
    if not room_id:
        missing.append("MATRIX_ROOM_ID")
    if not allowed_users:
        missing.append("DEVAGENT_ALLOWED_USERS")

    if missing:
        raise ValueError(f"missing required env values: {', '.join(missing)}")

    return MatrixWorkerConfig(
        homeserver_url=homeserver_url,
        access_token=access_token,
        room_id=room_id,
        allowed_users=allowed_users,
        state_file=os.getenv("DEVAGENT_MATRIX_STATE_FILE", "/srv/devagent/state/matrix_worker_state.json"),
        artifacts_root=os.getenv("DEVAGENT_ARTIFACTS_ROOT", "/srv/agent-artifacts"),
        poll_timeout_ms=int(os.getenv("DEVAGENT_MATRIX_SYNC_TIMEOUT_MS", "30000")),
        retry_sleep_seconds=float(os.getenv("DEVAGENT_MATRIX_RETRY_SLEEP_SECONDS", "2.0")),
        send_notices=os.getenv("DEVAGENT_MATRIX_SEND_NOTICES", "1") not in {"0", "false", "False"},
    )


def build_worker(config: MatrixWorkerConfig) -> MatrixWorker:
    runner = JobRunner()
    engine = DevAgentEngine(artifacts_root=config.artifacts_root, runner=runner)
    worktrees = WorktreeManager()
    jobs = JobService(engine=engine, worktrees=worktrees)
    client = MatrixClient(config.homeserver_url, config.access_token)
    return MatrixWorker(config=config, client=client, engine=engine, jobs=jobs, worktrees=worktrees)


def main() -> None:
    parser = argparse.ArgumentParser(description="DevAgent Matrix live worker")
    parser.add_argument("--once", action="store_true", help="process one sync cycle and exit")
    args = parser.parse_args()

    config = load_config_from_env()
    worker = build_worker(config)

    def _signal_handler(signum: int, _frame: object) -> None:
        print(f"matrix worker signal: {signum}")
        worker.stop()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    if args.once:
        sync = worker.client.sync(since=worker.state.since, timeout_ms=config.poll_timeout_ms)
        worker.process_sync_payload(sync.payload)
        worker.state.since = sync.next_batch
        worker.state.save(config.state_file)
        return

    worker.run_forever()


if __name__ == "__main__":
    main()
