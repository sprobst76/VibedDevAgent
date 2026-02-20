from __future__ import annotations

import subprocess
import unittest


class MatrixNoSudoScriptTests(unittest.TestCase):
    def test_script_is_syntax_valid(self) -> None:
        proc = subprocess.run(
            ["bash", "-n", "scripts/matrix_no_sudo_test.sh"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
