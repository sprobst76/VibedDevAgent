from __future__ import annotations

import subprocess
import unittest


class MatrixScriptsHelpTests(unittest.TestCase):
    def test_get_event_help(self) -> None:
        proc = subprocess.run(
            ["python3", "scripts/matrix_get_event.py", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--event-id", proc.stdout)

    def test_room_info_help(self) -> None:
        proc = subprocess.run(
            ["python3", "scripts/matrix_room_info.py", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--room-id", proc.stdout)


if __name__ == "__main__":
    unittest.main()
