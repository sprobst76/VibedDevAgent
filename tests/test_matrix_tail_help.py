from __future__ import annotations

import subprocess
import unittest


class MatrixTailHelpTests(unittest.TestCase):
    def test_tail_help(self) -> None:
        proc = subprocess.run(
            ["python3", "scripts/matrix_tail.py", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--room-id", proc.stdout)
        self.assertIn("--once", proc.stdout)


if __name__ == "__main__":
    unittest.main()
