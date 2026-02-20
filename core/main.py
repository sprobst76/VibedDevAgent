"""Minimal DevAgent core entrypoint with reaction flow demo."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from core.engine import DevAgentEngine
from core.security import parse_allowed_users
from core.startup_recovery import recover_stale_worktrees
from runner.job_runner import JobRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="DevAgent core scaffold")
    parser.add_argument("--job-id", default="00001")
    parser.add_argument("--reaction", default="✅", help="Matrix reaction emoji")
    parser.add_argument("--user-id", default="@alice:example.org")
    parser.add_argument("--allowed-users", default=os.getenv("DEVAGENT_ALLOWED_USERS", ""))
    parser.add_argument("--artifacts-root", default=os.getenv("DEVAGENT_ARTIFACTS_ROOT", "/tmp/devagent-artifacts"))
    parser.add_argument("--run-command", default=None, help="Optional command to start after approve")
    parser.add_argument("--run-cwd", default=os.getcwd(), help="Working directory for --run-command")
    parser.add_argument("--recover-worktrees-root", default=None, help="Optional root for stale worktree cleanup")
    parser.add_argument("--active-job-ids", default="", help="Comma-separated active job ids for recovery")
    args = parser.parse_args()

    allowed_users = parse_allowed_users(args.allowed_users)
    runner = JobRunner() if args.run_command else None
    engine = DevAgentEngine(artifacts_root=args.artifacts_root, runner=runner)
    if args.recover_worktrees_root:
        active_job_ids = {value.strip() for value in args.active_job_ids.split(",") if value.strip()}
        removed = recover_stale_worktrees(args.recover_worktrees_root, active_job_ids)
        if removed:
            print(f"recovery removed stale worktrees: {len(removed)}")

    engine.create_job(args.job_id)
    engine.advance_to_wait_approval(args.job_id)
    decision = engine.handle_matrix_reaction(
        job_id=args.job_id,
        reaction=args.reaction,
        user_id=args.user_id,
        allowed_users=allowed_users,
        run_command=args.run_command,
        run_cwd=args.run_cwd,
    )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    state = engine.get_job(args.job_id).state.value
    print(f"[{now}] reaction={args.reaction} accepted={decision.accepted} state={state} reason={decision.reason}")


if __name__ == "__main__":
    main()
