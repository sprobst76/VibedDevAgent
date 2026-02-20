from __future__ import annotations

import unittest


class MatrixSendJobcardScriptTests(unittest.TestCase):
    def test_mode_help_contains_text(self) -> None:
        # Lightweight guard: script help should expose mode choices.
        from subprocess import run

        proc = run(
            ["python3", "scripts/matrix_send_jobcard.py", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--mode", proc.stdout)
        self.assertIn("text", proc.stdout)


if __name__ == "__main__":
    unittest.main()
