"""AI task handler: passes Matrix messages to claude CLI and returns output."""

from __future__ import annotations

import logging
import os
import re
import select
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger("devagent.ai_handler")

# Worker splits output into Matrix-sized chunks; this is just a safety cap for huge outputs.
# Override with DEVAGENT_MAX_OUTPUT_CHARS in .env (default: 65536).
MAX_OUTPUT_CHARS: int = int(os.getenv("DEVAGENT_MAX_OUTPUT_CHARS", "65536"))

# Matches ANSI/VT escape sequences emitted by PTY-attached processes.
# Covers CSI sequences (ESC [ ... ) and simple two-byte ESC sequences.
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Remove ANSI terminal escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


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
    use_pty: bool = False,
) -> AiTaskResult:
    """Run claude --print with message in cwd and return the result.

    If cancel_event is provided and set while the task is running,
    the subprocess is terminated and a cancelled result is returned.

    If use_pty is True the subprocess is attached to a pseudo-terminal so that
    tools that require a TTY (e.g. interactive claude without --print) work
    correctly.  ANSI escape sequences and CR/LF normalization are applied
    automatically.  Falls back to pipe mode if pty is unavailable.
    """
    resolved = shutil.which(claude_bin) or claude_bin

    cmd = [resolved, "--print"]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.append(message)

    if use_pty:
        return _run_with_pty(cmd, cwd=cwd, timeout_seconds=timeout_seconds,
                             cancel_event=cancel_event, resolved=resolved)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Poll until done, timeout, or cancel
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


def _run_with_pty(
    cmd: list[str],
    *,
    cwd: str,
    timeout_seconds: int,
    cancel_event,
    resolved: str,
) -> AiTaskResult:
    """Run *cmd* attached to a pseudo-terminal and return collected output.

    The PTY makes the subprocess believe it is connected to a real terminal,
    which is important for programs that disable rich output or behave
    differently when stdout is a pipe.

    Output is collected from the master side, ANSI sequences are stripped,
    and CR/LF pairs are normalised to LF before returning.
    """
    try:
        import pty as _pty
    except ImportError:
        log.warning("pty module unavailable — falling back to pipe mode")
        # Redirect to pipe path with resolved binary
        return _run_pipe_fallback(cmd, cwd=cwd, timeout_seconds=timeout_seconds,
                                  cancel_event=cancel_event, resolved=resolved)

    master_fd = slave_fd = -1
    try:
        master_fd, slave_fd = _pty.openpty()

        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        # Parent does not need the slave side.
        os.close(slave_fd)
        slave_fd = -1

        chunks: list[bytes] = []
        elapsed = 0.0
        poll_interval = 0.1

        while True:
            ret = proc.poll()

            # Drain whatever is available right now (non-blocking).
            readable, _, _ = select.select([master_fd], [], [], 0.0)
            if readable:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        chunks.append(data)
                except OSError:
                    pass

            if ret is not None:
                # Drain any remaining buffered output.
                while True:
                    r, _, _ = select.select([master_fd], [], [], 0.1)
                    if not r:
                        break
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            chunks.append(data)
                        else:
                            break
                    except OSError:
                        break
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

        raw_bytes = b"".join(chunks)
        raw = raw_bytes.decode("utf-8", errors="replace")
        # PTY line endings: \r\n → \n
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        raw = _strip_ansi(raw).strip()
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
    finally:
        for fd in (master_fd, slave_fd):
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _run_pipe_fallback(
    cmd: list[str],
    *,
    cwd: str,
    timeout_seconds: int,
    cancel_event,
    resolved: str,
) -> AiTaskResult:
    """Minimal pipe-mode fallback used when pty is unavailable."""
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        elapsed = 0.0
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
                return AiTaskResult(success=False, output="🚫 Aufgabe abgebrochen.",
                                    truncated=False, exit_code=-2)
            if elapsed >= timeout_seconds:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return AiTaskResult(
                    success=False,
                    output=f"⏱ Timeout nach {timeout_seconds}s — Aufgabe abgebrochen.",
                    truncated=False, exit_code=-1,
                )
            time.sleep(0.5)
            elapsed += 0.5
        stdout, stderr = proc.communicate()
        raw = (stdout or "").strip() or (stderr or "").strip()
        truncated = len(raw) > MAX_OUTPUT_CHARS
        return AiTaskResult(success=proc.returncode == 0,
                            output=raw[:MAX_OUTPUT_CHARS] if truncated else raw,
                            truncated=truncated, exit_code=proc.returncode)
    except FileNotFoundError:
        return AiTaskResult(success=False,
                            output=f"claude CLI nicht gefunden ({resolved}). Bitte installieren.",
                            truncated=False, exit_code=-1)
