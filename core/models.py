"""Core domain models for job states and events."""

from __future__ import annotations

from enum import Enum


class JobState(str, Enum):
    RECEIVED = "RECEIVED"
    PLANNING = "PLANNING"
    WAIT_APPROVAL = "WAIT_APPROVAL"
    RUNNING = "RUNNING"
    RUN_TESTS = "RUN_TESTS"
    REVIEWING = "REVIEWING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobEvent(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    STOP = "stop"


TERMINAL_STATES = {JobState.DONE, JobState.FAILED, JobState.CANCELLED}
