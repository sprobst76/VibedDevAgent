"""AI task handler: passes Matrix messages to claude CLI and returns output."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from dataclasses import dataclass

# Worker splits output into Matrix-sized chunks; this is just a safety cap for huge outputs
MAX_OUTPUT_CHARS = 65536


@dataclass
class AiTaskResult:
    success: bool
    output: str
    truncated: bool
    exit_code: int


def parse_ai_message(body: str) -> tuple[str | None, str] | None:
    """Parse an !ai message into (repo_or_none, task).

    Supported formats:
        !ai <task>
        !ai @<repo> <task>

    Returns (repo, task) tuple or None if not an !ai message.
    """
    stripped = body.strip()
    lower = stripped.lower()
    if not lower.startswith("!ai "):
        return None

    rest = stripped[4:].strip()
    if not rest:
        return None

    if rest.startswith("@"):
        parts = rest.split(None, 1)
        repo = parts[0][1:]  # strip leading @
        task = parts[1].strip() if len(parts) > 1 else ""
        if not task:
            return None
        return repo, task

    return None, rest


def run_ai_task(
    *,
    message: str,
    cwd: str,
    claude_bin: str = "claude",
    timeout_seconds: int = 3600,
    skip_permissions: bool = True,
    cancel_event=None,  # threading.Event — set to cancel
) -> AiTaskResult:
    """Run claude --print with message in cwd and return the result.

    If cancel_event is provided and set while the task is running,
    the subprocess is terminated and a cancelled result is returned.
    """
    resolved = shutil.which(claude_bin) or claude_bin

    cmd = [resolved, "--print"]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.append(message)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Poll until done, timeout, or cancel
        import time
        elapsed = 0.0
        poll_interval = 0.5

        while True:
            ret = proc.poll()
            if ret is not None:
                break

            if cancel_event is not None and cancel_event.is_set():
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return AiTaskResult(
                    success=False,
                    output="🚫 Aufgabe abgebrochen.",
                    truncated=False,
                    exit_code=-2,
                )

            if elapsed >= timeout_seconds:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return AiTaskResult(
                    success=False,
                    output=f"⏱ Timeout nach {timeout_seconds}s — Aufgabe abgebrochen.",
                    truncated=False,
                    exit_code=-1,
                )

            time.sleep(poll_interval)
            elapsed += poll_interval

        stdout, stderr = proc.communicate()
        raw = (stdout or "").strip()
        if not raw:
            raw = (stderr or "").strip()
        truncated = len(raw) > MAX_OUTPUT_CHARS
        return AiTaskResult(
            success=proc.returncode == 0,
            output=raw[:MAX_OUTPUT_CHARS] if truncated else raw,
            truncated=truncated,
            exit_code=proc.returncode,
        )

    except FileNotFoundError:
        return AiTaskResult(
            success=False,
            output=f"claude CLI nicht gefunden ({resolved}). Bitte installieren.",
            truncated=False,
            exit_code=-1,
        )
