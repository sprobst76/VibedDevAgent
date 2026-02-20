"""Review report formatting in required A-E structure."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewReport:
    commands: list[str]
    expected_output: list[str]
    files_changed: list[str]
    verify_steps: list[str]
    rollback_steps: list[str]

    def to_text(self) -> str:
        sections = [
            "A) COMMANDS\n" + "\n".join(self.commands or ["- none"]),
            "B) EXPECTED OUTPUT\n" + "\n".join(self.expected_output or ["- none"]),
            "C) FILES CHANGED\n" + "\n".join(self.files_changed or ["- none"]),
            "D) VERIFY\n" + "\n".join(self.verify_steps or ["- none"]),
            "E) ROLLBACK\n" + "\n".join(self.rollback_steps or ["- none"]),
        ]
        return "\n\n".join(sections)
