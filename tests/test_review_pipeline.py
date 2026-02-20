from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runner.review.classification import classify_error
from runner.review.hooks import load_hooks
from runner.review.report import ReviewReport


class ReviewPipelineTests(unittest.TestCase):
    def test_report_format_contains_sections(self) -> None:
        report = ReviewReport(
            commands=["pytest -q"],
            expected_output=["all tests passed"],
            files_changed=["core/main.py"],
            verify_steps=["run tests"],
            rollback_steps=["git revert <sha>"],
        )
        text = report.to_text()
        self.assertIn("A) COMMANDS", text)
        self.assertIn("E) ROLLBACK", text)

    def test_load_hooks_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "hooks.json"
            config.write_text(json.dumps({"hooks": ["make test", "make lint"]}), encoding="utf-8")
            hooks = load_hooks(str(config))
            self.assertEqual(hooks, ["make test", "make lint"])

    def test_error_classification(self) -> None:
        self.assertEqual(classify_error("connection timeout to upstream"), "infra")
        self.assertEqual(classify_error("pytest assert failed"), "test")
        self.assertEqual(classify_error("Traceback: TypeError"), "code")
        self.assertEqual(classify_error("tmux command not found"), "tool")


if __name__ == "__main__":
    unittest.main()
