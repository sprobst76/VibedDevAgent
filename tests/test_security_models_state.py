"""Tests for core/security.py, core/models.py, core/state_machine.py."""
from __future__ import annotations

import unittest

from core.models import TERMINAL_STATES, JobEvent, JobState
from core.security import is_user_allowed, parse_allowed_users
from core.state_machine import TRANSITIONS, apply_event


# ── parse_allowed_users ───────────────────────────────────────────────────────

class ParseAllowedUsersTests(unittest.TestCase):
    def test_empty_string_returns_empty_set(self) -> None:
        self.assertEqual(parse_allowed_users(""), set())

    def test_none_returns_empty_set(self) -> None:
        self.assertEqual(parse_allowed_users(None), set())

    def test_single_user(self) -> None:
        self.assertEqual(parse_allowed_users("@alice:matrix.org"), {"@alice:matrix.org"})

    def test_multiple_users_comma_separated(self) -> None:
        result = parse_allowed_users("@alice:matrix.org,@bob:matrix.org")
        self.assertEqual(result, {"@alice:matrix.org", "@bob:matrix.org"})

    def test_strips_surrounding_whitespace(self) -> None:
        result = parse_allowed_users("  @alice:matrix.org ,  @bob:matrix.org  ")
        self.assertEqual(result, {"@alice:matrix.org", "@bob:matrix.org"})

    def test_deduplicates_entries(self) -> None:
        result = parse_allowed_users("@alice:matrix.org,@alice:matrix.org")
        self.assertEqual(result, {"@alice:matrix.org"})

    def test_ignores_empty_segments(self) -> None:
        result = parse_allowed_users(",@alice:matrix.org,,@bob:matrix.org,")
        self.assertEqual(result, {"@alice:matrix.org", "@bob:matrix.org"})

    def test_returns_set_not_list(self) -> None:
        self.assertIsInstance(parse_allowed_users("@u:m.org"), set)


# ── is_user_allowed ───────────────────────────────────────────────────────────

class IsUserAllowedTests(unittest.TestCase):
    def test_allowed_user(self) -> None:
        self.assertTrue(is_user_allowed("@alice:matrix.org", {"@alice:matrix.org"}))

    def test_disallowed_user(self) -> None:
        self.assertFalse(is_user_allowed("@mallory:matrix.org", {"@alice:matrix.org"}))

    def test_empty_allowed_set_denies_all(self) -> None:
        self.assertFalse(is_user_allowed("@alice:matrix.org", set()))

    def test_exact_match_required(self) -> None:
        # Partial match must not work
        self.assertFalse(is_user_allowed("@alice", {"@alice:matrix.org"}))


# ── JobState / JobEvent / TERMINAL_STATES ─────────────────────────────────────

class ModelsTests(unittest.TestCase):
    def test_terminal_states_contains_done_failed_cancelled(self) -> None:
        self.assertIn(JobState.DONE, TERMINAL_STATES)
        self.assertIn(JobState.FAILED, TERMINAL_STATES)
        self.assertIn(JobState.CANCELLED, TERMINAL_STATES)

    def test_active_states_not_in_terminal(self) -> None:
        for state in (JobState.RECEIVED, JobState.PLANNING,
                      JobState.WAIT_APPROVAL, JobState.RUNNING,
                      JobState.RUN_TESTS, JobState.REVIEWING):
            self.assertNotIn(state, TERMINAL_STATES, f"{state} should not be terminal")

    def test_job_state_enum_values(self) -> None:
        self.assertEqual(JobState.WAIT_APPROVAL.value, "WAIT_APPROVAL")
        self.assertEqual(JobState.RUNNING.value, "RUNNING")
        self.assertEqual(JobState.CANCELLED.value, "CANCELLED")

    def test_job_event_enum_values(self) -> None:
        self.assertEqual(JobEvent.APPROVE.value, "approve")
        self.assertEqual(JobEvent.REJECT.value, "reject")
        self.assertEqual(JobEvent.STOP.value, "stop")

    def test_job_state_is_string_enum(self) -> None:
        # JobState(str, Enum) lets it be used as a plain string
        self.assertEqual(JobState.RUNNING, "RUNNING")


# ── apply_event (state machine) ───────────────────────────────────────────────

class StateMachineFullTests(unittest.TestCase):
    # Valid transitions
    def test_wait_approval_approve_to_running(self) -> None:
        d = apply_event(JobState.WAIT_APPROVAL, JobEvent.APPROVE)
        self.assertTrue(d.allowed)
        self.assertEqual(d.state_after, JobState.RUNNING)

    def test_wait_approval_reject_to_cancelled(self) -> None:
        d = apply_event(JobState.WAIT_APPROVAL, JobEvent.REJECT)
        self.assertTrue(d.allowed)
        self.assertEqual(d.state_after, JobState.CANCELLED)

    def test_planning_reject_to_cancelled(self) -> None:
        d = apply_event(JobState.PLANNING, JobEvent.REJECT)
        self.assertTrue(d.allowed)
        self.assertEqual(d.state_after, JobState.CANCELLED)

    def test_running_stop_to_cancelled(self) -> None:
        d = apply_event(JobState.RUNNING, JobEvent.STOP)
        self.assertTrue(d.allowed)
        self.assertEqual(d.state_after, JobState.CANCELLED)

    def test_run_tests_stop_to_cancelled(self) -> None:
        d = apply_event(JobState.RUN_TESTS, JobEvent.STOP)
        self.assertTrue(d.allowed)
        self.assertEqual(d.state_after, JobState.CANCELLED)

    def test_reviewing_stop_to_cancelled(self) -> None:
        d = apply_event(JobState.REVIEWING, JobEvent.STOP)
        self.assertTrue(d.allowed)
        self.assertEqual(d.state_after, JobState.CANCELLED)

    # Invalid transitions
    def test_running_approve_rejected(self) -> None:
        d = apply_event(JobState.RUNNING, JobEvent.APPROVE)
        self.assertFalse(d.allowed)
        self.assertEqual(d.state_after, JobState.RUNNING)  # unchanged

    def test_received_approve_rejected(self) -> None:
        d = apply_event(JobState.RECEIVED, JobEvent.APPROVE)
        self.assertFalse(d.allowed)

    def test_cancelled_approve_rejected(self) -> None:
        d = apply_event(JobState.CANCELLED, JobEvent.APPROVE)
        self.assertFalse(d.allowed)

    def test_done_any_event_rejected(self) -> None:
        for event in JobEvent:
            d = apply_event(JobState.DONE, event)
            self.assertFalse(d.allowed, f"DONE should reject {event}")

    def test_failed_any_event_rejected(self) -> None:
        for event in JobEvent:
            d = apply_event(JobState.FAILED, event)
            self.assertFalse(d.allowed, f"FAILED should reject {event}")

    def test_cancelled_any_event_rejected(self) -> None:
        for event in JobEvent:
            d = apply_event(JobState.CANCELLED, event)
            self.assertFalse(d.allowed, f"CANCELLED should reject {event}")

    def test_reason_describes_problem_on_rejection(self) -> None:
        d = apply_event(JobState.DONE, JobEvent.APPROVE)
        self.assertIn("not allowed", d.reason)
        self.assertIn("DONE", d.reason)

    def test_all_defined_transitions_are_allowed(self) -> None:
        """Every entry in TRANSITIONS must actually be allowed."""
        for (state, event), new_state in TRANSITIONS.items():
            d = apply_event(state, event)
            self.assertTrue(d.allowed, f"transition {state}+{event} should be allowed")
            self.assertEqual(d.state_after, new_state)

    def test_state_preserved_on_rejection(self) -> None:
        d = apply_event(JobState.RUNNING, JobEvent.APPROVE)
        self.assertFalse(d.allowed)
        self.assertEqual(d.state_before, JobState.RUNNING)
        self.assertEqual(d.state_after, JobState.RUNNING)


if __name__ == "__main__":
    unittest.main()
