"""High-level runner orchestration around tmux sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from runner.tmux_driver import TmuxDriver


@dataclass(frozen=True)
class JobRunSpec:
    job_id: str
    command: str
    cwd: str
    artifacts_root: str = "/srv/agent-artifacts"


@dataclass(frozen=True)
class JobRunHandle:
    job_id: str
    session_name: str
    log_file: str


class JobRunner:
    def __init__(self, tmux: TmuxDriver | None = None) -> None:
        self.tmux = tmux or TmuxDriver()

    def start(self, spec: JobRunSpec) -> JobRunHandle:
        log_file = str(Path(spec.artifacts_root) / f"job-{spec.job_id}" / "runner.log")
        session_name = self.tmux.start_session(
            job_id=spec.job_id,
            command=spec.command,
            cwd=spec.cwd,
            log_file=log_file,
        )
        return JobRunHandle(job_id=spec.job_id, session_name=session_name, log_file=log_file)

    def stop(self, *, job_id: str) -> bool:
        self.tmux.send_interrupt(job_id=job_id)
        return self.tmux.stop_session(job_id=job_id)

    def tail(self, *, job_id: str, lines: int = 200) -> str:
        return self.tmux.capture_output(job_id=job_id, lines=lines)
