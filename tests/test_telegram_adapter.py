from __future__ import annotations

import tempfile
import unittest

from adapters.telegram.commands import parse_command
from adapters.telegram.controller import handle_command
from core.engine import DevAgentEngine


class TelegramAdapterTests(unittest.TestCase):
    def test_parse_command(self) -> None:
        cmd = parse_command("/approve 123")
        self.assertIsNotNone(cmd)
        assert cmd is not None
        self.assertEqual(cmd.name, "approve")
        self.assertEqual(cmd.job_id, "123")
        self.assertIsNone(parse_command("approve 123"))
        self.assertIsNone(parse_command("/invalid 123"))

    def test_status_and_approve_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = DevAgentEngine(artifacts_root=tmp)
            engine.create_job("123")
            engine.advance_to_wait_approval("123")

            status_cmd = parse_command("/status 123")
            approve_cmd = parse_command("/approve 123")
            assert status_cmd is not None and approve_cmd is not None

            status = handle_command(
                engine=engine,
                command=status_cmd,
                user_id="@alice:example.org",
                allowed_users={"@alice:example.org"},
            )
            self.assertTrue(status.accepted)
            self.assertIn("WAIT_APPROVAL", status.message)

            approved = handle_command(
                engine=engine,
                command=approve_cmd,
                user_id="@alice:example.org",
                allowed_users={"@alice:example.org"},
            )
            self.assertTrue(approved.accepted)
            self.assertIn("RUNNING", approved.message)


if __name__ == "__main__":
    unittest.main()
