"""Long-running DevAgent service process."""

from __future__ import annotations

import argparse
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ServiceState:
    running: bool = True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_service(*, interval_seconds: float = 5.0, once: bool = False) -> None:
    state = ServiceState(running=True)

    def _handle_signal(signum: int, _frame: object) -> None:
        state.running = False
        print(f"[{_utc_now()}] service signal received: {signum}")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    print(f"[{_utc_now()}] devagent service started")

    if once:
        print(f"[{_utc_now()}] devagent service one-shot exit")
        return

    while state.running:
        time.sleep(interval_seconds)

    print(f"[{_utc_now()}] devagent service stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="DevAgent long-running service")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run_service(interval_seconds=args.interval, once=args.once)


if __name__ == "__main__":
    main()
