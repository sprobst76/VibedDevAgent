"""Tests for new ui/server.py routes and helpers.

These tests require FastAPI (installed via requirements-ui.txt).
They are skipped automatically when FastAPI is not available.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import fastapi  # noqa: F401
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False


@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed (run: pip install -r requirements-ui.txt)")
class TestValidJobId(unittest.TestCase):
    def setUp(self):
        from ui.server import _valid_job_id
        self._valid = _valid_job_id

    def test_simple_alphanumeric(self):
        self.assertTrue(self._valid("abc123"))

    def test_with_hyphens_underscores(self):
        self.assertTrue(self._valid("job-abc_123"))

    def test_empty_string_rejected(self):
        self.assertFalse(self._valid(""))

    def test_path_traversal_rejected(self):
        self.assertFalse(self._valid("../etc/passwd"))
        self.assertFalse(self._valid("../../secret"))

    def test_slash_rejected(self):
        self.assertFalse(self._valid("job/evil"))

    def test_too_long_rejected(self):
        self.assertFalse(self._valid("a" * 129))

    def test_max_length_allowed(self):
        self.assertTrue(self._valid("a" * 128))


@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestGetRecentJobs(unittest.TestCase):
    def test_reads_first_and_last_lines(self):
        from ui.server import _get_recent_jobs

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job-xyz789"
            job_dir.mkdir()
            audit = job_dir / "audit.jsonl"
            audit.write_text(
                json.dumps({"job_id": "xyz789", "action": "job_created",
                            "user_id": "@alice:example.org", "state_after": "WAIT_APPROVAL",
                            "timestamp": "2026-01-01T10:00:00Z",
                            "extra": {"repo": "MyProject"}}) + "\n" +
                json.dumps({"job_id": "xyz789", "action": "runner_start",
                            "user_id": "@alice:example.org", "state_after": "RUNNING",
                            "timestamp": "2026-01-01T10:05:00Z", "extra": {}}) + "\n",
                encoding="utf-8",
            )
            with patch("ui.server.ARTIFACTS_ROOT", tmp):
                jobs = _get_recent_jobs(limit=10)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["job_id"], "xyz789")
        self.assertEqual(job["project"], "MyProject")
        self.assertEqual(job["requested_by"], "@alice:example.org")
        self.assertEqual(job["state"], "RUNNING")
        self.assertEqual(job["created_at"], "2026-01-01T10:00:00Z")
        self.assertEqual(job["updated_at"], "2026-01-01T10:05:00Z")
        self.assertEqual(job["short_id"], "xyz789"[:8])

    def test_empty_artifacts_root_returns_empty(self):
        from ui.server import _get_recent_jobs

        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "nonexistent"
            with patch("ui.server.ARTIFACTS_ROOT", str(empty)):
                jobs = _get_recent_jobs()
        self.assertEqual(jobs, [])


@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestGetJobAudit(unittest.TestCase):
    def test_returns_all_lines(self):
        from ui.server import _get_job_audit

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job-audit1"
            job_dir.mkdir()
            lines = [
                {"job_id": "audit1", "action": "job_created", "state_after": "WAIT_APPROVAL"},
                {"job_id": "audit1", "action": "approve",     "state_after": "RUNNING"},
                {"job_id": "audit1", "action": "runner_stop", "state_after": "DONE"},
            ]
            (job_dir / "audit.jsonl").write_text(
                "\n".join(json.dumps(ln) for ln in lines) + "\n", encoding="utf-8"
            )
            with patch("ui.server.ARTIFACTS_ROOT", tmp):
                result = _get_job_audit("audit1")

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["action"], "job_created")
        self.assertEqual(result[-1]["state_after"], "DONE")

    def test_missing_job_returns_empty(self):
        from ui.server import _get_job_audit

        with tempfile.TemporaryDirectory() as tmp:
            with patch("ui.server.ARTIFACTS_ROOT", tmp):
                result = _get_job_audit("no-such-job")
        self.assertEqual(result, [])
