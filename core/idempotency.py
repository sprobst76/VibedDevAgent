"""Simple in-memory idempotency store."""

from __future__ import annotations


class IdempotencyStore:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    def mark_once(self, key: str) -> bool:
        """Return True once per key; False for duplicates."""
        if key in self._keys:
            return False
        self._keys.add(key)
        return True
