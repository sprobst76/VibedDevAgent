from __future__ import annotations

import unittest

from core.event_push import EventDispatcher, PushFilter


class EventPushTests(unittest.TestCase):
    def test_dispatcher_filters_status(self) -> None:
        received: list[dict[str, str]] = []
        dispatcher = EventDispatcher(PushFilter(statuses={"FAILED", "DONE", "WAIT_APPROVAL"}))
        dispatcher.subscribe(lambda event: received.append(event))

        self.assertTrue(dispatcher.publish({"status": "FAILED", "job_id": "1"}))
        self.assertFalse(dispatcher.publish({"status": "RUNNING", "job_id": "1"}))
        self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()
