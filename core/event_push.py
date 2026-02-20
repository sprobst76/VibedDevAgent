"""Optional event push dispatcher with status filtering."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PushFilter:
    statuses: set[str]

    def matches(self, status: str) -> bool:
        return status in self.statuses


class EventDispatcher:
    def __init__(self, push_filter: PushFilter) -> None:
        self.push_filter = push_filter
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers.append(callback)

    def publish(self, event: dict[str, Any]) -> bool:
        status = str(event.get("status", ""))
        if not self.push_filter.matches(status):
            return False

        for callback in self._subscribers:
            callback(event)
        return True
