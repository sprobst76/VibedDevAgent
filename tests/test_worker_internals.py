"""Tests for MatrixWorker internals: room-map, state persistence, config, output labeling."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters.matrix.client import MatrixSyncResult
from core.engine import DevAgentEngine
from core.job_service import JobService
from core.matrix_worker import (
    MatrixWorker,
    MatrixWorkerConfig,
    MatrixWorkerState,
    load_config_from_env,
)
from runner.job_runner import JobRunHandle


# ── Shared helpers ────────────────────────────────────────────────────────────

class FakeMatrixClient:
    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []
        self.messages: list[tuple[str, str]] = []
        self._login_user: str = ""

    def sync(self, *, since, timeout_ms=30000) -> MatrixSyncResult:
        return MatrixSyncResult(next_batch=since or "t0", payload={"rooms": {"join": {}}})

    def send_notice(self, *, room_id: str, body: str) -> dict:
        self.notices.append((room_id, body))
        return {}

    def send_message(self, *, room_id: str, body: str, msgtype="m.text") -> dict:
        self.messages.append((room_id, body))
        return {}


class FakeRunner:
    def start(self, spec): return JobRunHandle(job_id=spec.job_id, session_name="s", log_file="/tmp/l")
    def stop(self, *, job_id): return True


class FakeWorktrees:
    def create(self, repo, job_id, base_branch="main"): return f"/tmp/{repo}/{job_id}"
    def cleanup(self, repo, job_id): return "ok"


def _make_worker(tmp: str, room_id: str = "!room:matrix.org") -> tuple[MatrixWorker, FakeMatrixClient]:
    cfg = MatrixWorkerConfig(
        homeserver_url="https://matrix.org",
        access_token="tok",
        room_id=room_id,
        allowed_users={"@alice:matrix.org"},
        state_file=f"{tmp}/state.json",
        artifacts_root=tmp,
    )
    client = FakeMatrixClient()
    engine = DevAgentEngine(artifacts_root=tmp, runner=FakeRunner())  # type: ignore[arg-type]
    worker = MatrixWorker(
        config=cfg, client=client,
        engine=engine,
        jobs=JobService(engine=engine, worktrees=FakeWorktrees()),  # type: ignore[arg-type]
        worktrees=FakeWorktrees(),  # type: ignore[arg-type]
    )
    return worker, client


# ── MatrixWorkerState persistence ─────────────────────────────────────────────

class WorkerStateTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/state.json"
            state = MatrixWorkerState(
                since="s999",
                jobcards={"$ev": {"job_id": "1", "repo": "r", "branch": "main",
                                   "command": "c", "requested_by": "@u:m.org",
                                   "worktree_path": "/wt", "room_id": "!r:m.org"}},
                job_states={"1": "RUNNING"},
            )
            state.save(path)
            loaded = MatrixWorkerState.load(path)
            self.assertEqual(loaded.since, "s999")
            self.assertIn("$ev", loaded.jobcards)
            self.assertEqual(loaded.job_states["1"], "RUNNING")

    def test_load_nonexistent_returns_empty(self) -> None:
        state = MatrixWorkerState.load("/nonexistent/path/state.json")
        self.assertIsNone(state.since)
        self.assertEqual(state.jobcards, {})
        self.assertEqual(state.job_states, {})

    def test_save_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/deep/nested/state.json"
            MatrixWorkerState(since="s1").save(path)
            self.assertTrue(Path(path).exists())

    def test_save_is_atomic_tmp_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/state.json"
            MatrixWorkerState(since="s1").save(path)
            # tmp file should not exist after atomic replace
            self.assertFalse(Path(f"{tmp}/state.tmp").exists())
            self.assertTrue(Path(path).exists())

    def test_corrupted_state_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/state.json"
            Path(path).write_text("NOT VALID JSON", encoding="utf-8")
            state = MatrixWorkerState.load(path)
            self.assertIsNone(state.since)


# ── _refresh_room_map ─────────────────────────────────────────────────────────

class RefreshRoomMapTests(unittest.TestCase):
    def _projects_json(self, tmp: str, projects: dict) -> str:
        path = f"{tmp}/projects.json"
        Path(path).write_text(
            json.dumps({"projects": projects}), encoding="utf-8"
        )
        return path

    def test_builds_room_to_project_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pf = self._projects_json(tmp, {
                "app":    {"matrix_room_id": "!aaa:m.org", "local_path": "/app"},
                "api":    {"matrix_room_id": "!bbb:m.org", "local_path": "/api"},
                "noroom": {"matrix_room_id": "",            "local_path": "/x"},
            })
            worker, _ = _make_worker(tmp)
            worker.config = worker.config.__class__(
                **{**worker.config.__dict__, "projects_file": pf}
            )
            worker._refresh_room_map()
            self.assertEqual(worker._room_map["!aaa:m.org"], "app")
            self.assertEqual(worker._room_map["!bbb:m.org"], "api")
            self.assertNotIn("", worker._room_map)

    def test_empty_projects_file_clears_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pf = self._projects_json(tmp, {})
            worker, _ = _make_worker(tmp)
            worker._room_map = {"!old:m.org": "old"}
            worker.config = worker.config.__class__(
                **{**worker.config.__dict__, "projects_file": pf}
            )
            worker._refresh_room_map()
            self.assertEqual(worker._room_map, {})

    def test_missing_projects_file_clears_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, _ = _make_worker(tmp)
            worker.config = worker.config.__class__(
                **{**worker.config.__dict__, "projects_file": "/nonexistent/p.json"}
            )
            worker._refresh_room_map()
            self.assertEqual(worker._room_map, {})


# ── _active_rooms ─────────────────────────────────────────────────────────────

class ActiveRoomsTests(unittest.TestCase):
    def test_includes_primary_room(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, _ = _make_worker(tmp, room_id="!primary:m.org")
            self.assertIn("!primary:m.org", worker._active_rooms())

    def test_includes_project_rooms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, _ = _make_worker(tmp)
            worker._room_map = {"!proj1:m.org": "app1", "!proj2:m.org": "app2"}
            rooms = worker._active_rooms()
            self.assertIn("!proj1:m.org", rooms)
            self.assertIn("!proj2:m.org", rooms)

    def test_union_of_primary_and_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, _ = _make_worker(tmp, room_id="!primary:m.org")
            worker._room_map = {"!proj:m.org": "app"}
            rooms = worker._active_rooms()
            self.assertEqual(rooms, {"!primary:m.org", "!proj:m.org"})

    def test_no_primary_room_returns_only_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, _ = _make_worker(tmp, room_id="")
            worker._room_map = {"!proj:m.org": "app"}
            rooms = worker._active_rooms()
            self.assertEqual(rooms, {"!proj:m.org"})


# ── _run_ai_task_async output labeling ───────────────────────────────────────

class OutputLabelingTests(unittest.TestCase):
    def _run_async(self, output: str, truncated: bool = False, tmp: str | None = None) -> list[str]:
        """Run _run_ai_task_async with mocked run_ai_task and collect notices."""
        from adapters.matrix.ai_handler import AiTaskResult
        import threading

        with tempfile.TemporaryDirectory() as t:
            tmp = tmp or t
            worker, client = _make_worker(tmp)
            lock = threading.Lock()
            lock.acquire()

            with patch("core.matrix_worker.run_ai_task") as mock_run:
                mock_run.return_value = AiTaskResult(
                    success=True, output=output, truncated=truncated, exit_code=0
                )
                worker._run_ai_task_async(
                    "task", "/cwd", "ctx", "!room:matrix.org", "@alice:matrix.org",
                    history=None, room_lock=lock, cancel_event=None,
                )
            return [body for _, body in client.notices]

    def test_single_chunk_no_label(self) -> None:
        notices = self._run_async("short output")
        self.assertEqual(len(notices), 1)
        self.assertNotIn("Teil", notices[0])

    def test_multi_chunk_has_part_labels(self) -> None:
        # Build output that splits into multiple paragraphs over 3800 chars
        big_output = "\n\n".join(["x" * 2000, "y" * 2000, "z" * 2000])
        notices = self._run_async(big_output)
        self.assertGreater(len(notices), 1)
        # All but the last should have [Teil N/M]
        for n in notices:
            if "Teil" in n:
                self.assertIn("/", n)  # format: [Teil N/M]

    def test_success_prefix_only_on_first_chunk(self) -> None:
        big_output = "\n\n".join(["a" * 2000, "b" * 2000, "c" * 2000])
        notices = self._run_async(big_output)
        self.assertIn("✅", notices[0])
        for n in notices[1:]:
            self.assertNotIn("✅", n)

    def test_truncated_sends_extra_notice(self) -> None:
        notices = self._run_async("short but truncated", truncated=True)
        combined = " ".join(notices)
        self.assertIn("gekürzt", combined)

    def test_failed_task_uses_cross_prefix(self) -> None:
        from adapters.matrix.ai_handler import AiTaskResult
        with tempfile.TemporaryDirectory() as tmp:
            worker, client = _make_worker(tmp)
            lock = threading.Lock()
            lock.acquire()
            with patch("core.matrix_worker.run_ai_task") as mock_run:
                mock_run.return_value = AiTaskResult(
                    success=False, output="error msg", truncated=False, exit_code=1
                )
                worker._run_ai_task_async(
                    "task", "/cwd", "ctx", "!room:matrix.org", "@alice:matrix.org",
                    history=None, room_lock=lock, cancel_event=None,
                )
            self.assertTrue(any("❌" in b for _, b in client.notices))

    def test_room_lock_released_after_task(self) -> None:
        from adapters.matrix.ai_handler import AiTaskResult
        with tempfile.TemporaryDirectory() as tmp:
            worker, _ = _make_worker(tmp)
            lock = threading.Lock()
            lock.acquire()
            with patch("core.matrix_worker.run_ai_task") as mock_run:
                mock_run.return_value = AiTaskResult(
                    success=True, output="done", truncated=False, exit_code=0
                )
                worker._run_ai_task_async(
                    "t", "/", "c", "!room:matrix.org", "@u:m.org",
                    history=None, room_lock=lock, cancel_event=None,
                )
            # lock should be released now
            acquired = lock.acquire(blocking=False)
            self.assertTrue(acquired, "lock was not released after task completion")
            lock.release()

    def test_room_lock_released_even_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, _ = _make_worker(tmp)
            lock = threading.Lock()
            lock.acquire()
            with patch("core.matrix_worker.run_ai_task", side_effect=RuntimeError("boom")):
                try:
                    worker._run_ai_task_async(
                        "t", "/", "c", "!room:matrix.org", "@u:m.org",
                        history=None, room_lock=lock, cancel_event=None,
                    )
                except Exception:
                    pass
            acquired = lock.acquire(blocking=False)
            self.assertTrue(acquired, "lock must be released even after exception")
            lock.release()


# ── load_config_from_env ──────────────────────────────────────────────────────

class LoadConfigFromEnvTests(unittest.TestCase):
    def _env(self, **extra) -> dict:
        base = {
            "MATRIX_HOMESERVER_URL": "https://matrix.org",
            "MATRIX_ACCESS_TOKEN": "tok123",
            "DEVAGENT_ALLOWED_USERS": "@alice:matrix.org",
        }
        base.update(extra)
        return base

    def test_minimal_config_loads(self) -> None:
        with patch.dict(os.environ, self._env(), clear=True):
            cfg = load_config_from_env()
        self.assertEqual(cfg.homeserver_url, "https://matrix.org")
        self.assertEqual(cfg.access_token, "tok123")
        self.assertIn("@alice:matrix.org", cfg.allowed_users)

    def test_missing_homeserver_raises(self) -> None:
        env = self._env()
        del env["MATRIX_HOMESERVER_URL"]
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_config_from_env()
        self.assertIn("MATRIX_HOMESERVER_URL", str(ctx.exception))

    def test_missing_token_raises(self) -> None:
        env = self._env()
        del env["MATRIX_ACCESS_TOKEN"]
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_config_from_env()
        self.assertIn("MATRIX_ACCESS_TOKEN", str(ctx.exception))

    def test_missing_allowed_users_raises(self) -> None:
        env = self._env()
        del env["DEVAGENT_ALLOWED_USERS"]
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_config_from_env()
        self.assertIn("DEVAGENT_ALLOWED_USERS", str(ctx.exception))

    def test_relogin_user_from_devagent_env_var(self) -> None:
        env = self._env(
            MATRIX_USER_DEVAGENT="@bot:matrix.org",
            MATRIX_PASSWORD_DEVAGENT="s3cr3t",
        )
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config_from_env()
        self.assertEqual(cfg.relogin_user, "@bot:matrix.org")
        self.assertEqual(cfg.relogin_password, "s3cr3t")

    def test_relogin_user_empty_when_not_set(self) -> None:
        """When MATRIX_USER_DEVAGENT is absent relogin_user defaults to empty string."""
        env = self._env()  # no relogin vars
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config_from_env()
        self.assertEqual(cfg.relogin_user, "")
        self.assertEqual(cfg.relogin_password, "")

    def test_relogin_password_loaded_from_env(self) -> None:
        env = self._env(
            MATRIX_USER_DEVAGENT="@bot:matrix.org",
            MATRIX_PASSWORD_DEVAGENT="secret_pw",
        )
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config_from_env()
        self.assertEqual(cfg.relogin_user, "@bot:matrix.org")
        self.assertEqual(cfg.relogin_password, "secret_pw")

    def test_ai_timeout_from_env(self) -> None:
        env = self._env(DEVAGENT_AI_TIMEOUT_SECONDS="7200")
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config_from_env()
        self.assertEqual(cfg.ai_timeout_seconds, 7200)

    def test_history_context_built_for_task(self) -> None:
        """History lines are prepended to the task message when history is non-empty."""
        from adapters.matrix.ai_handler import AiTaskResult
        with tempfile.TemporaryDirectory() as tmp:
            worker, client = _make_worker(tmp)
            history = [
                {"name": "alice", "body": "hello"},
                {"name": "bot",   "body": "hi there"},
            ]
            lock = threading.Lock()
            lock.acquire()
            captured_message: list[str] = []
            with patch("core.matrix_worker.run_ai_task") as mock_run:
                def capture(**kwargs):
                    captured_message.append(kwargs.get("message", ""))
                    return AiTaskResult(success=True, output="ok", truncated=False, exit_code=0)
                mock_run.side_effect = capture
                worker._run_ai_task_async(
                    "do something", "/cwd", "ctx", "!room:matrix.org", "@alice:matrix.org",
                    history=history, room_lock=lock, cancel_event=None,
                )
            self.assertEqual(len(captured_message), 1)
            msg = captured_message[0]
            self.assertIn("Gesprächsverlauf", msg)
            self.assertIn("alice: hello", msg)
            self.assertIn("bot: hi there", msg)
            self.assertIn("do something", msg)


# ── _warn_if_no_relogin ────────────────────────────────────────────────────────

class WarnIfNoReloginTests(unittest.TestCase):
    def test_sends_matrix_message_when_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, client = _make_worker(tmp, room_id="!main:m.org")
            client._login_user = ""
            worker._warn_if_no_relogin()
            self.assertEqual(len(client.messages), 1)
            room_id, body = client.messages[0]
            self.assertEqual(room_id, "!main:m.org")
            self.assertIn("Auto-Relogin", body)
            self.assertIn("MATRIX_USER_DEVAGENT", body)

    def test_no_message_when_credentials_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, client = _make_worker(tmp)
            client._login_user = "@bot:m.org"
            worker._warn_if_no_relogin()
            self.assertEqual(client.messages, [])

    def test_matrix_error_does_not_crash_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, client = _make_worker(tmp)
            client._login_user = ""

            def _raise(*_args, **_kwargs):
                raise RuntimeError("network error")

            client.send_message = _raise  # type: ignore[method-assign]
            # Should not raise
            worker._warn_if_no_relogin()


if __name__ == "__main__":
    unittest.main()
