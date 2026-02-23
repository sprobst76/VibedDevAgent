"""Tests for MatrixWorker: _split_for_matrix, _record_history, commands, per-room lock."""
from __future__ import annotations

import json
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path

from adapters.matrix.client import MatrixSyncResult
from core.engine import DevAgentEngine
from core.job_service import JobService
from core.matrix_worker import MatrixWorker, MatrixWorkerConfig
from runner.job_runner import JobRunHandle


# ── Shared fakes ──────────────────────────────────────────────────────────────

class FakeMatrixClient:
    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []

    def sync(self, *, since, timeout_ms=30000) -> MatrixSyncResult:
        return MatrixSyncResult(next_batch=since or "tok0", payload={"rooms": {"join": {}}})

    def send_notice(self, *, room_id: str, body: str) -> dict:
        self.notices.append((room_id, body))
        return {"event_id": "$n"}

    def send_message(self, *, room_id: str, body: str, msgtype: str = "m.text") -> dict:
        return {"event_id": "$m"}

    def last_notice(self) -> str | None:
        return self.notices[-1][1] if self.notices else None

    def notices_for(self, room_id: str) -> list[str]:
        return [body for rid, body in self.notices if rid == room_id]


class FakeRunner:
    def start(self, spec):
        return JobRunHandle(job_id=spec.job_id, session_name="s", log_file="/tmp/l")

    def stop(self, *, job_id: str) -> bool:
        return True


class FakeWorktrees:
    def create(self, repo, job_id, base_branch="main") -> str:
        return f"/tmp/{repo}/{job_id}"

    def cleanup(self, repo, job_id) -> str:
        return "ok"


def _make_worker(tmp: str, room_id: str = "!room:matrix.org") -> tuple[MatrixWorker, FakeMatrixClient]:
    cfg = MatrixWorkerConfig(
        homeserver_url="https://matrix.org",
        access_token="tok",
        room_id=room_id,
        allowed_users={"@alice:matrix.org"},
        state_file=f"{tmp}/state.json",
        artifacts_root=tmp,
        send_notices=True,
    )
    client = FakeMatrixClient()
    engine = DevAgentEngine(artifacts_root=tmp, runner=FakeRunner())  # type: ignore[arg-type]
    worktrees = FakeWorktrees()
    jobs = JobService(engine=engine, worktrees=worktrees)  # type: ignore[arg-type]
    worker = MatrixWorker(config=cfg, client=client, engine=engine, jobs=jobs, worktrees=worktrees)  # type: ignore[arg-type]
    return worker, client


def _msg_event(body: str, sender: str = "@alice:matrix.org", room: str = "!room:matrix.org") -> dict:
    return {
        "event_id": "$e1",
        "type": "m.room.message",
        "sender": sender,
        "room_id": room,
        "content": {"msgtype": "m.text", "body": body},
    }


# ── _split_for_matrix ─────────────────────────────────────────────────────────

class SplitForMatrixTests(unittest.TestCase):
    def test_short_text_returns_single_chunk(self) -> None:
        text = "hello world"
        result = MatrixWorker._split_for_matrix(text, max_chars=3800)
        self.assertEqual(result, [text])

    def test_exact_max_length_returns_single_chunk(self) -> None:
        text = "a" * 3800
        self.assertEqual(MatrixWorker._split_for_matrix(text, max_chars=3800), [text])

    def test_splits_at_paragraph_boundary(self) -> None:
        para1 = "a" * 100
        para2 = "b" * 100
        text = para1 + "\n\n" + para2
        # max_chars just small enough to force a split
        result = MatrixWorker._split_for_matrix(text, max_chars=150)
        self.assertEqual(len(result), 2)
        self.assertIn(para1, result[0])
        self.assertIn(para2, result[1])

    def test_multiple_paragraphs_packed_greedily(self) -> None:
        # Three short paragraphs that all fit in 150 chars together
        text = "aaa\n\nbbb\n\nccc"
        result = MatrixWorker._split_for_matrix(text, max_chars=200)
        self.assertEqual(len(result), 1)

    def test_long_single_paragraph_hard_cuts(self) -> None:
        text = "x" * 8000
        result = MatrixWorker._split_for_matrix(text, max_chars=3800)
        self.assertEqual(len(result), 3)   # ceil(8000 / 3800) = 3
        for chunk in result[:-1]:
            self.assertEqual(len(chunk), 3800)

    def test_empty_string_returns_single_chunk(self) -> None:
        self.assertEqual(MatrixWorker._split_for_matrix("", max_chars=3800), [""])

    def test_all_chunks_within_max_chars(self) -> None:
        # Stress test with mixed paragraph sizes
        parts = ["x" * 2000, "y" * 2000, "z" * 500, "w" * 5000]
        text = "\n\n".join(parts)
        result = MatrixWorker._split_for_matrix(text, max_chars=3800)
        for chunk in result:
            self.assertLessEqual(len(chunk), 3800, f"chunk too long: {len(chunk)}")

    def test_two_chunks_both_non_empty(self) -> None:
        para1 = "A" * 3000
        para2 = "B" * 3000
        result = MatrixWorker._split_for_matrix(para1 + "\n\n" + para2, max_chars=3800)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(len(c) > 0 for c in result))

    def test_hard_cut_prefers_line_boundary(self) -> None:
        # Single paragraph: 3700 a's + newline + 200 b's = 3901 chars total
        # The newline is at position 3700, well past the midpoint → split there
        line1 = "a" * 3700 + "\n"
        line2 = "b" * 200
        result = MatrixWorker._split_for_matrix(line1 + line2, max_chars=3800)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], line1)
        self.assertEqual(result[1], line2)

    def test_hard_cut_prefers_word_boundary(self) -> None:
        # 3700 x's + space + 199 y's = 3900 chars, space at position 3700
        # Should split just after the space, not at hard 3800
        text = "x" * 3700 + " " + "y" * 199
        result = MatrixWorker._split_for_matrix(text, max_chars=3800)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "x" * 3700 + " ")
        self.assertEqual(result[1], "y" * 199)

    def test_hard_cut_falls_back_when_no_boundary_in_first_half(self) -> None:
        # Space only in first 10% of chunk → hard cut, no shift
        text = "x" * 100 + " " + "x" * 3900  # space at 100, rest fills 3900+
        result = MatrixWorker._split_for_matrix(text, max_chars=3800)
        for chunk in result:
            self.assertLessEqual(len(chunk), 3800)


# ── _record_history ───────────────────────────────────────────────────────────

class RecordHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.worker, _ = _make_worker(self.tmp)
        self.room = "!room:matrix.org"

    def _history(self) -> list[dict]:
        return list(self.worker._room_history.get(self.room, []))

    def _event(self, body: str, sender: str = "@alice:matrix.org") -> dict:
        return {
            "type": "m.room.message",
            "room_id": self.room,
            "sender": sender,
            "content": {"body": body},
        }

    def test_records_plain_message(self) -> None:
        self.worker._record_history(self._event("Hello there"))
        h = self._history()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["body"], "Hello there")
        self.assertEqual(h[0]["name"], "alice")  # localpart of @alice:matrix.org

    def test_skips_working_notice(self) -> None:
        self.worker._record_history(self._event("🤔 Arbeite daran [ctx]…"))
        self.assertEqual(len(self._history()), 0)

    def test_skips_empty_body(self) -> None:
        self.worker._record_history(self._event(""))
        self.assertEqual(len(self._history()), 0)

    def test_rolling_window_respects_history_size(self) -> None:
        self.worker._history_size = 3
        for i in range(10):
            self.worker._record_history(self._event(f"msg {i}"))
        h = self._history()
        self.assertLessEqual(len(h), 3)
        # Last 3 messages should be kept
        self.assertEqual(h[-1]["body"], "msg 9")

    def test_multiple_rooms_tracked_independently(self) -> None:
        room_b = "!other:matrix.org"
        ev_a = dict(self._event("from A"), room_id=self.room)
        ev_b = dict(self._event("from B"), room_id=room_b)
        self.worker._record_history(ev_a)
        self.worker._record_history(ev_b)
        self.assertEqual(len(self._history()), 1)
        self.assertEqual(len(list(self.worker._room_history.get(room_b, []))), 1)

    def test_body_truncated_in_history(self) -> None:
        long_body = "z" * 1000
        self.worker._record_history(self._event(long_body))
        stored = self._history()[0]["body"]
        self.assertLessEqual(len(stored), 600)


# ── process_event: commands ───────────────────────────────────────────────────

class WorkerCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.worker, self.client = _make_worker(self.tmp)
        self.room = "!room:matrix.org"

    def test_status_command_sends_notice(self) -> None:
        self.worker.process_event(_msg_event("!status"))
        notices = self.client.notices_for(self.room)
        self.assertTrue(any("Kein Task aktiv" in n for n in notices))

    def test_status_case_insensitive(self) -> None:
        self.worker.process_event(_msg_event("!STATUS"))
        notices = self.client.notices_for(self.room)
        self.assertTrue(any("status" in n.lower() for n in notices))

    def test_status_shows_uptime(self) -> None:
        self.worker.process_event(_msg_event("!Status"))
        body = self.client.last_notice() or ""
        self.assertIn("Uptime", body)

    def test_cancel_when_idle_says_no_task(self) -> None:
        self.worker.process_event(_msg_event("!cancel"))
        body = self.client.last_notice() or ""
        self.assertIn("Kein Task aktiv", body)

    def test_cancel_case_insensitive(self) -> None:
        self.worker.process_event(_msg_event("!CANCEL"))
        body = self.client.last_notice() or ""
        self.assertIn("Kein Task aktiv", body)

    def test_cancel_sets_cancel_event_when_running(self) -> None:
        # Simulate a running task: set up lock + cancel event
        lock = threading.Lock()
        lock.acquire()
        cancel = threading.Event()
        self.worker._room_locks[self.room] = lock
        self.worker._room_cancel[self.room] = cancel
        self.worker._room_task_start[self.room] = 0.0

        self.worker.process_event(_msg_event("!cancel"))
        self.assertTrue(cancel.is_set())
        lock.release()

    def test_help_command_lists_commands(self) -> None:
        self.worker.process_event(_msg_event("!help"))
        body = self.client.last_notice() or ""
        self.assertIn("!ai", body)
        self.assertIn("!status", body)
        self.assertIn("!cancel", body)

    def test_help_case_insensitive(self) -> None:
        self.worker.process_event(_msg_event("!HELP"))
        self.assertIsNotNone(self.client.last_notice())

    def test_plain_message_sends_hint(self) -> None:
        self.worker.process_event(_msg_event("just a plain message"))
        body = self.client.last_notice() or ""
        self.assertIn("!ai", body)
        self.assertIn("nicht verarbeitet", body)

    def test_unknown_bang_command_is_silent(self) -> None:
        self.worker.process_event(_msg_event("!unknowncmd"))
        # No notice should be sent for unknown ! commands
        self.assertEqual(len(self.client.notices), 0)

    def test_unauthorized_sender_ignored(self) -> None:
        self.worker.process_event(
            _msg_event("!help", sender="@stranger:matrix.org")
        )
        self.assertEqual(len(self.client.notices), 0)

    def test_unauthorized_sender_plain_message_ignored(self) -> None:
        # plain messages from unauthorized users get no hint
        self.worker.process_event(
            _msg_event("hello", sender="@intruder:matrix.org")
        )
        self.assertEqual(len(self.client.notices), 0)

    def test_ai_message_rejected_when_room_busy(self) -> None:
        # Lock the room as if a task is running
        lock = threading.Lock()
        lock.acquire()
        self.worker._room_locks[self.room] = lock

        self.worker.process_event(_msg_event("!ai do something"))
        body = self.client.last_notice() or ""
        self.assertIn("läuft bereits", body)
        lock.release()

    def test_ai_case_insensitive(self) -> None:
        """!AI should be routed same as !ai (dispatched to executor)."""
        # We just verify no "nicht verarbeitet" hint is sent
        # The task would run async but we can check the working notice was sent
        self.worker.process_event(_msg_event("!AI write hello world"))
        notices = self.client.notices_for(self.room)
        # Either "Arbeite" notice (dispatched) or nothing unexpected
        sent_bodies = " ".join(notices)
        self.assertNotIn("nicht verarbeitet", sent_bodies)

    def test_history_excludes_working_notices(self) -> None:
        """Working notices must not pollute the conversation history."""
        self.worker._record_history({
            "type": "m.room.message",
            "room_id": self.room,
            "sender": "@bot:matrix.org",
            "content": {"body": "🤔 Arbeite daran [ctx]…"},
        })
        h = list(self.worker._room_history.get(self.room, []))
        self.assertEqual(len(h), 0)

    def test_status_shows_room_count(self) -> None:
        self.worker.process_event(_msg_event("!status"))
        body = self.client.last_notice() or ""
        self.assertIn("Räume", body)

    def test_cancel_lock_released_reports_no_task(self) -> None:
        """If the lock was held but has since been released, report idle."""
        lock = threading.Lock()
        # lock is NOT held — task finished
        self.worker._room_locks[self.room] = lock
        cancel = threading.Event()
        self.worker._room_cancel[self.room] = cancel

        self.worker.process_event(_msg_event("!cancel"))
        body = self.client.last_notice() or ""
        self.assertIn("Kein Task aktiv", body)
        self.assertFalse(cancel.is_set())  # cancel was NOT set

    def test_ai_message_with_missing_repo_sends_error(self) -> None:
        """!ai @nonexistent-repo task should send an error notice."""
        self.worker.process_event(_msg_event("!ai @nonexistent_repo_xyz do something"))
        bodies = " ".join(self.client.notices_for(self.room))
        self.assertIn("nicht gefunden", bodies)

    def test_status_shows_sync_count(self) -> None:
        self.worker._sync_count = 42
        self.worker.process_event(_msg_event("!status"))
        body = self.client.last_notice() or ""
        self.assertIn("42", body)

    def test_status_shows_running_task_info(self) -> None:
        """When a task is running, !status should mention it."""
        lock = threading.Lock()
        lock.acquire()
        self.worker._room_locks[self.room] = lock
        self.worker._room_task_start[self.room] = 0.0  # started at epoch

        self.worker.process_event(_msg_event("!status"))
        body = self.client.last_notice() or ""
        self.assertIn("Task läuft", body)
        lock.release()


# ── process_sync_payload ──────────────────────────────────────────────────────

class ProcessSyncPayloadTests(unittest.TestCase):
    def test_events_from_watched_room_are_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, client = _make_worker(tmp, room_id="!watched:matrix.org")
            payload = {
                "rooms": {
                    "join": {
                        "!watched:matrix.org": {
                            "timeline": {
                                "events": [_msg_event("!help", room="!watched:matrix.org")]
                            }
                        }
                    }
                }
            }
            worker.process_sync_payload(payload)
            self.assertGreater(len(client.notices), 0)

    def test_events_from_unwatched_room_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker, client = _make_worker(tmp, room_id="!watched:matrix.org")
            payload = {
                "rooms": {
                    "join": {
                        "!other:matrix.org": {
                            "timeline": {
                                "events": [_msg_event("!help", room="!other:matrix.org")]
                            }
                        }
                    }
                }
            }
            worker.process_sync_payload(payload)
            self.assertEqual(len(client.notices), 0)


_SAMPLE_TODO = textwrap.dedent("""\
    ## P0 -- Basis
    - [x] Fertig
    - [ ] Noch offen

    ## P1 -- Extras
    - [ ] Extra-Item
""")


class HandleTodoCommandTests(unittest.TestCase):
    """Tests for !todo, !todo @<project>, and fallback behaviour."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.worker, self.client = _make_worker(self.tmp)
        self.room = "!room:matrix.org"

    def _make_projects_file(self, projects: dict) -> str:
        path = Path(self.tmp) / "projects.json"
        path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
        return str(path)

    def _make_project_with_todo(self, name: str, todo_content: str) -> str:
        proj_dir = Path(self.tmp) / name
        proj_dir.mkdir(exist_ok=True)
        (proj_dir / "TODO.md").write_text(todo_content, encoding="utf-8")
        return str(proj_dir)

    def test_todo_without_arg_sends_notice(self) -> None:
        """!todo with no projects file falls back to DevAgent's own TODO (or sends error)."""
        # No projects file → falls back to DevAgent TODO which may not exist in tmp
        # → sends a "not found" notice or a summary
        self.worker.process_event(_msg_event("!todo"))
        self.assertGreater(len(self.client.notices), 0)

    def test_todo_project_summary_lists_project_names(self) -> None:
        """!todo with projects registered should include project names."""
        proj_path = self._make_project_with_todo("MyProject", _SAMPLE_TODO)
        projects_path = self._make_projects_file({
            "MyProject": {"local_path": proj_path},
        })
        # Point worker at this projects file
        self.worker.config = self.worker.config.__class__(
            **{**self.worker.config.__dict__, "projects_file": projects_path}
        )
        self.worker.process_event(_msg_event("!todo"))
        bodies = " ".join(self.client.notices_for(self.room))
        self.assertIn("MyProject", bodies)

    def test_todo_at_project_shows_project_todos(self) -> None:
        """!todo @MyProject should show that project's open TODOs."""
        proj_path = self._make_project_with_todo("MyProject", _SAMPLE_TODO)
        projects_path = self._make_projects_file({
            "MyProject": {"local_path": proj_path},
        })
        from dataclasses import replace
        self.worker.config = replace(self.worker.config, projects_file=projects_path)
        self.worker.process_event(_msg_event("!todo @MyProject"))
        bodies = " ".join(self.client.notices_for(self.room))
        self.assertIn("MyProject", bodies)
        self.assertIn("Noch offen", bodies)

    def test_todo_at_unknown_project_sends_error(self) -> None:
        """!todo @NoSuchProject should send an error notice."""
        projects_path = self._make_projects_file({})
        from dataclasses import replace
        self.worker.config = replace(self.worker.config, projects_file=projects_path)
        self.worker.process_event(_msg_event("!todo @NoSuchProject"))
        bodies = " ".join(self.client.notices_for(self.room))
        self.assertIn("NoSuchProject", bodies)

    def test_todo_at_project_without_todo_md_sends_fallback(self) -> None:
        """!todo @EmptyProject (no TODO.md) should send a 'not found' notice."""
        empty_dir = Path(self.tmp) / "EmptyProject"
        empty_dir.mkdir(exist_ok=True)
        projects_path = self._make_projects_file({
            "EmptyProject": {"local_path": str(empty_dir)},
        })
        from dataclasses import replace
        self.worker.config = replace(self.worker.config, projects_file=projects_path)
        self.worker.process_event(_msg_event("!todo @EmptyProject"))
        bodies = " ".join(self.client.notices_for(self.room))
        # Should mention the project or "not found"
        self.assertTrue("EmptyProject" in bodies or "gefunden" in bodies)

    def test_todos_alias_also_works(self) -> None:
        """!todos should route to the same handler as !todo."""
        self.worker.process_event(_msg_event("!todos"))
        self.assertGreater(len(self.client.notices), 0)


if __name__ == "__main__":
    unittest.main()
