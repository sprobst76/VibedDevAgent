"""Tests for audit.py, jobcard.py, listener.py, reactions.py edge cases."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adapters.matrix.jobcard import JobCard, build_jobcard_event
from adapters.matrix.listener import MatrixListenerConfig, MatrixRoomListener
from adapters.matrix.reactions import evaluate_reaction, map_reaction_to_event
from core.audit import append_audit_event
from core.models import JobEvent, JobState


# ── append_audit_event ────────────────────────────────────────────────────────

class AuditTests(unittest.TestCase):
    def test_creates_job_dir_and_audit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = append_audit_event(
                artifacts_root=tmp,
                job_id="audit-1",
                action="job_created",
                user_id="@alice:matrix.org",
                state_before="RECEIVED",
                state_after="WAIT_APPROVAL",
                allowed=True,
                reason="ok",
            )
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "audit.jsonl")

    def test_appended_record_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            append_audit_event(
                artifacts_root=tmp, job_id="j1", action="approve",
                user_id="@u:m.org", state_before="WAIT_APPROVAL",
                state_after="RUNNING", allowed=True, reason="ok",
            )
            line = (Path(tmp) / "job-j1" / "audit.jsonl").read_text(encoding="utf-8").strip()
            record = json.loads(line)
            self.assertEqual(record["job_id"], "j1")
            self.assertEqual(record["action"], "approve")
            self.assertTrue(record["allowed"])

    def test_appends_multiple_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for action in ["approve", "runner_start", "runner_stop"]:
                append_audit_event(
                    artifacts_root=tmp, job_id="j2", action=action,
                    user_id="@u:m.org", state_before="RUNNING",
                    state_after="RUNNING", allowed=True, reason="ok",
                )
            lines = (Path(tmp) / "job-j2" / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 3)
            actions = [json.loads(l)["action"] for l in lines]
            self.assertEqual(actions, ["approve", "runner_start", "runner_stop"])

    def test_extra_field_included_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            append_audit_event(
                artifacts_root=tmp, job_id="j3", action="start",
                user_id="@u:m.org", state_before="A", state_after="B",
                allowed=True, reason="x",
                extra={"session_name": "dev-j3", "log_file": "/tmp/log"},
            )
            line = (Path(tmp) / "job-j3" / "audit.jsonl").read_text(encoding="utf-8").strip()
            record = json.loads(line)
            self.assertIn("extra", record)
            self.assertEqual(record["extra"]["session_name"], "dev-j3")

    def test_extra_field_absent_when_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            append_audit_event(
                artifacts_root=tmp, job_id="j4", action="x",
                user_id="@u:m.org", state_before="A", state_after="A",
                allowed=False, reason="nope",
            )
            line = (Path(tmp) / "job-j4" / "audit.jsonl").read_text(encoding="utf-8").strip()
            record = json.loads(line)
            self.assertNotIn("extra", record)

    def test_timestamp_present_and_non_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            append_audit_event(
                artifacts_root=tmp, job_id="j5", action="x",
                user_id="@u:m.org", state_before="A", state_after="A",
                allowed=True, reason="ok",
            )
            line = (Path(tmp) / "job-j5" / "audit.jsonl").read_text(encoding="utf-8").strip()
            record = json.loads(line)
            self.assertTrue(record.get("timestamp"))

    def test_creates_nested_dir_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            append_audit_event(
                artifacts_root=tmp, job_id="nested-99",
                action="x", user_id="@u:m.org",
                state_before="A", state_after="A",
                allowed=True, reason="ok",
            )
            self.assertTrue((Path(tmp) / "job-nested-99").is_dir())


# ── JobCard.from_matrix_event ─────────────────────────────────────────────────

class JobCardParseTests(unittest.TestCase):
    def _valid_event(self, **overrides) -> dict:
        event = {
            "type": "devagent.jobcard",
            "room_id": "!room:matrix.org",
            "sender": "@alice:matrix.org",
            "content": {
                "job_id": "42",
                "repo": "myrepo",
                "branch": "main",
                "command": "make test",
                "requested_by": "@alice:matrix.org",
                "created_at": "2026-01-01T00:00:00Z",
            },
        }
        event.update(overrides)
        return event

    def test_valid_event_parses_correctly(self) -> None:
        card = JobCard.from_matrix_event(self._valid_event())
        self.assertEqual(card.job_id, "42")
        self.assertEqual(card.repo, "myrepo")
        self.assertEqual(card.room_id, "!room:matrix.org")

    def test_wrong_event_type_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            JobCard.from_matrix_event(self._valid_event(type="m.room.message"))
        self.assertIn("unsupported", str(ctx.exception))

    def test_missing_room_id_raises(self) -> None:
        event = self._valid_event()
        del event["room_id"]
        with self.assertRaises(ValueError):
            JobCard.from_matrix_event(event)

    def test_empty_room_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            JobCard.from_matrix_event(self._valid_event(room_id=""))

    def test_missing_content_raises(self) -> None:
        event = self._valid_event()
        del event["content"]
        with self.assertRaises(ValueError):
            JobCard.from_matrix_event(event)

    def test_non_dict_content_raises(self) -> None:
        with self.assertRaises(ValueError):
            JobCard.from_matrix_event(self._valid_event(content="not a dict"))

    def test_each_missing_required_field_raises(self) -> None:
        required = ["job_id", "repo", "branch", "command", "requested_by", "created_at"]
        for field in required:
            event = self._valid_event()
            del event["content"][field]
            with self.assertRaises(ValueError, msg=f"missing {field} should raise"):
                JobCard.from_matrix_event(event)

    def test_empty_required_field_raises(self) -> None:
        event = self._valid_event()
        event["content"]["repo"] = ""
        with self.assertRaises(ValueError):
            JobCard.from_matrix_event(event)

    def test_all_fields_coerced_to_str(self) -> None:
        event = self._valid_event()
        event["content"]["job_id"] = 99  # numeric id
        card = JobCard.from_matrix_event(event)
        self.assertIsInstance(card.job_id, str)
        self.assertEqual(card.job_id, "99")


# ── build_jobcard_event ───────────────────────────────────────────────────────

class BuildJobcardEventTests(unittest.TestCase):
    def test_event_type_is_correct(self) -> None:
        ev = build_jobcard_event(
            room_id="!r:m.org", job_id="1", repo="r", branch="main",
            command="cmd", requested_by="@u:m.org",
        )
        self.assertEqual(ev["type"], "devagent.jobcard")

    def test_content_has_all_fields(self) -> None:
        ev = build_jobcard_event(
            room_id="!r:m.org", job_id="1", repo="r", branch="main",
            command="cmd", requested_by="@u:m.org",
        )
        for field in ("job_id", "repo", "branch", "command", "requested_by", "created_at"):
            self.assertIn(field, ev["content"])

    def test_created_at_ends_with_Z(self) -> None:
        ev = build_jobcard_event(
            room_id="!r:m.org", job_id="1", repo="r", branch="main",
            command="cmd", requested_by="@u:m.org",
        )
        self.assertTrue(ev["content"]["created_at"].endswith("Z"))

    def test_roundtrip_through_from_matrix_event(self) -> None:
        ev = build_jobcard_event(
            room_id="!r:m.org", job_id="99", repo="demo", branch="dev",
            command="pytest", requested_by="@u:m.org",
        )
        card = JobCard.from_matrix_event(ev)
        self.assertEqual(card.job_id, "99")
        self.assertEqual(card.branch, "dev")


# ── MatrixRoomListener edge cases ─────────────────────────────────────────────

class ListenerEdgeCaseTests(unittest.TestCase):
    def _listener(self) -> MatrixRoomListener:
        return MatrixRoomListener(
            MatrixListenerConfig(
                room_id="!room:matrix.org",
                allowed_senders={"@alice:matrix.org"},
            )
        )

    def test_non_jobcard_event_type_returns_none(self) -> None:
        event = {
            "type": "m.room.message",
            "room_id": "!room:matrix.org",
            "sender": "@alice:matrix.org",
            "content": {},
        }
        self.assertIsNone(self._listener().extract_job_request(event))

    def test_malformed_content_returns_none(self) -> None:
        event = {
            "type": "devagent.jobcard",
            "room_id": "!room:matrix.org",
            "sender": "@alice:matrix.org",
            "content": {"job_id": "1"},  # missing required fields
        }
        self.assertIsNone(self._listener().extract_job_request(event))

    def test_open_sender_list_accepts_any(self) -> None:
        listener = MatrixRoomListener(
            MatrixListenerConfig(
                room_id="!room:matrix.org",
                allowed_senders=set(),  # empty = no filter
            )
        )
        ev = build_jobcard_event(
            room_id="!room:matrix.org", job_id="1", repo="r",
            branch="main", command="c", requested_by="@any:matrix.org",
        )
        ev["sender"] = "@any:matrix.org"
        self.assertIsNotNone(listener.extract_job_request(ev))


# ── map_reaction_to_event / evaluate_reaction ─────────────────────────────────

class ReactionsExtendedTests(unittest.TestCase):
    def test_variation_selector_stripped_checkmark(self) -> None:
        """✅ with U+FE0F variation selector must still map to APPROVE."""
        self.assertEqual(map_reaction_to_event("✅\ufe0f"), JobEvent.APPROVE)

    def test_variation_selector_stripped_stop(self) -> None:
        self.assertEqual(map_reaction_to_event("🛑\ufe0f"), JobEvent.STOP)

    def test_variation_selector_stripped_cross(self) -> None:
        self.assertEqual(map_reaction_to_event("❌\ufe0f"), JobEvent.REJECT)

    def test_unknown_emoji_returns_none(self) -> None:
        self.assertIsNone(map_reaction_to_event("🎉"))
        self.assertIsNone(map_reaction_to_event("👍"))
        self.assertIsNone(map_reaction_to_event(""))

    def test_evaluate_unknown_reaction_not_accepted(self) -> None:
        d = evaluate_reaction(
            reaction="🎉",
            state=JobState.WAIT_APPROVAL,
            user_id="@u:m.org",
            allowed_users={"@u:m.org"},
        )
        self.assertFalse(d.accepted)
        self.assertIn("unsupported", d.reason)

    def test_evaluate_valid_reaction_with_variation_selector(self) -> None:
        """Reactions from Element (which appends U+FE0F) must be accepted."""
        d = evaluate_reaction(
            reaction="✅\ufe0f",
            state=JobState.WAIT_APPROVAL,
            user_id="@alice:m.org",
            allowed_users={"@alice:m.org"},
        )
        self.assertTrue(d.accepted)
        self.assertEqual(d.event, JobEvent.APPROVE)

    def test_approve_returns_correct_transition(self) -> None:
        d = evaluate_reaction(
            reaction="✅",
            state=JobState.WAIT_APPROVAL,
            user_id="@alice:m.org",
            allowed_users={"@alice:m.org"},
        )
        self.assertTrue(d.accepted)
        self.assertIsNotNone(d.transition)
        self.assertEqual(d.transition.state_after, JobState.RUNNING)  # type: ignore[union-attr]

    def test_reject_from_wait_approval_returns_cancelled(self) -> None:
        d = evaluate_reaction(
            reaction="❌",
            state=JobState.WAIT_APPROVAL,
            user_id="@alice:m.org",
            allowed_users={"@alice:m.org"},
        )
        self.assertTrue(d.accepted)
        self.assertEqual(d.transition.state_after, JobState.CANCELLED)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
