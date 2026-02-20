from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.startup_recovery import recover_stale_worktrees


class StartupRecoveryTests(unittest.TestCase):
    def test_recover_stale_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            keep = repo / "job-100"
            remove = repo / "job-200"
            keep.mkdir(parents=True)
            remove.mkdir(parents=True)

            removed = recover_stale_worktrees(str(root), {"100"})

            self.assertTrue(keep.exists())
            self.assertFalse(remove.exists())
            self.assertEqual(len(removed), 1)
            self.assertIn("job-200", removed[0])


if __name__ == "__main__":
    unittest.main()
