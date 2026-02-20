from __future__ import annotations

import unittest

from adapters.matrix.jobcard import JobCard, build_jobcard_event
from adapters.matrix.listener import MatrixListenerConfig, MatrixRoomListener


class MatrixJobCardTests(unittest.TestCase):
    def test_build_and_parse_jobcard(self) -> None:
        payload = build_jobcard_event(
            room_id="!room:example.org",
            job_id="00017",
            repo="my-repo",
            branch="main",
            command="make test",
            requested_by="@alice:example.org",
        )
        payload["sender"] = "@alice:example.org"

        card = JobCard.from_matrix_event(payload)
        self.assertEqual(card.job_id, "00017")
        self.assertEqual(card.repo, "my-repo")

    def test_listener_filters_room_and_sender(self) -> None:
        payload = build_jobcard_event(
            room_id="!room:example.org",
            job_id="00018",
            repo="my-repo",
            branch="main",
            command="make lint",
            requested_by="@alice:example.org",
        )
        payload["sender"] = "@alice:example.org"

        listener = MatrixRoomListener(
            MatrixListenerConfig(
                room_id="!room:example.org",
                allowed_senders={"@alice:example.org"},
            )
        )
        self.assertIsNotNone(listener.extract_job_request(payload))

        payload_wrong_room = dict(payload)
        payload_wrong_room["room_id"] = "!other:example.org"
        self.assertIsNone(listener.extract_job_request(payload_wrong_room))

        payload_wrong_sender = dict(payload)
        payload_wrong_sender["sender"] = "@mallory:example.org"
        self.assertIsNone(listener.extract_job_request(payload_wrong_sender))


if __name__ == "__main__":
    unittest.main()
