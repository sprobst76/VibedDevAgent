from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ui.generate_dashboard_data import generate_dashboard_data


class UiDashboardDataTests(unittest.TestCase):
    def test_generate_dashboard_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "job-123"
            job_dir.mkdir(parents=True)
            (job_dir / "audit.jsonl").write_text(
                '{"job_id":"123","state_after":"RUNNING","action":"approve","timestamp":"2026-02-20T00:00:00Z"}\n',
                encoding="utf-8",
            )

            out = root / "jobs.json"
            generate_dashboard_data(str(root), str(out))

            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["jobs"][0]["job_id"], "123")
            self.assertEqual(payload["jobs"][0]["state"], "RUNNING")


if __name__ == "__main__":
    unittest.main()
