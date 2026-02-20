"""Simple error classification for review summaries."""

from __future__ import annotations


def classify_error(message: str) -> str:
    text = message.lower()
    if any(word in text for word in ["timeout", "connection", "dns", "socket"]):
        return "infra"
    if any(word in text for word in ["pytest", "assert", "test failed", "unittest"]):
        return "test"
    if any(word in text for word in ["traceback", "exception", "syntaxerror", "typeerror"]):
        return "code"
    if any(word in text for word in ["tmux", "git", "command not found", "permission denied"]):
        return "tool"
    return "unknown"
