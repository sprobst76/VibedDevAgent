"""tmux driver used to control isolated job sessions."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from core.reliability import run_with_retry


class TmuxUnavailableError(RuntimeError):
    """Raised when tmux is not available on PATH."""


class TmuxDriver:
    def __init__(self, tmux_bin: str = "tmux", timeout_seconds: float = 15.0) -> None:
        self.tmux_bin = tmux_bin
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def session_name(job_id: str) -> str:
        return f"devagent-job-{job_id}"

    def _run_tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        def _op() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self.tmux_bin, *args],
                check=check,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )

        return run_with_retry(_op, retries=1, delay_seconds=0.05, retry_on=(subprocess.TimeoutExpired,))

    def ensure_available(self) -> None:
        try:
            self._run_tmux("-V")
        except FileNotFoundError as exc:
            raise TmuxUnavailableError("tmux binary not found on PATH") from exc

    def start_session(self, *, job_id: str, command: str, cwd: str, log_file: str) -> str:
        self.ensure_available()
        session = self.session_name(job_id)
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)

        wrapped = f"{command} 2>&1 | tee -a {shlex.quote(log_file)}"
        self._run_tmux("new-session", "-d", "-s", session, "-c", cwd, wrapped)
        return session

    def send_interrupt(self, *, job_id: str) -> None:
        session = self.session_name(job_id)
        self._run_tmux("send-keys", "-t", session, "C-c", check=False)

    def stop_session(self, *, job_id: str) -> bool:
        session = self.session_name(job_id)
        result = self._run_tmux("kill-session", "-t", session, check=False)
        return result.returncode == 0

    def capture_output(self, *, job_id: str, lines: int = 200) -> str:
        session = self.session_name(job_id)
        start_arg = f"-{lines}"
        result = self._run_tmux("capture-pane", "-p", "-S", start_arg, "-t", session, check=False)
        if result.returncode != 0:
            return ""
        return result.stdout
