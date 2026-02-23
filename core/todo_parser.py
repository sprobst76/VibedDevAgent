"""Parse TODO.md into structured data for Matrix and Web UI."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Priority section headers like "## P0 -- ..." or "## P1-SECURITY -- ..."
_SECTION_RE  = re.compile(r"^##\s+(P\d+(?:-\w+)?)\s*(?:--|—)?\s*(.*)")
# Subsection headers like "### 14) Option C: ..."
_SUB_RE      = re.compile(r"^###\s+\d+\)\s*(.*)")
_OPEN_RE     = re.compile(r"^\s*-\s+\[\s\]\s+(.*)")
_DONE_RE     = re.compile(r"^\s*-\s+\[[xX]\]\s+(.*)")

# Colour / emoji per priority for Matrix output
_PRIORITY_EMOJI: dict[str, str] = {
    "P0":           "🔵",
    "P1":           "🟢",
    "P1-SECURITY":  "🔴",
    "P2":           "🟣",
    "P3":           "🟡",
}


@dataclass
class TodoSection:
    priority: str          # e.g. "P0", "P1-SECURITY"
    title: str             # rest of the ## header
    open_items: list[str]  = field(default_factory=list)
    done_count: int        = 0

    @property
    def total(self) -> int:
        return self.done_count + len(self.open_items)

    @property
    def emoji(self) -> str:
        return _PRIORITY_EMOJI.get(self.priority, "⚪")


def parse_todo_file(path: str | Path) -> list[TodoSection]:
    """Read *path* and return one TodoSection per ## priority block."""
    sections: list[TodoSection] = []
    current: TodoSection | None = None

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        # New priority section
        m = _SECTION_RE.match(line)
        if m:
            current = TodoSection(priority=m.group(1), title=m.group(2).strip())
            sections.append(current)
            continue

        if current is None:
            continue

        # Open item
        m = _OPEN_RE.match(line)
        if m:
            current.open_items.append(m.group(1).strip())
            continue

        # Done item
        if _DONE_RE.match(line):
            current.done_count += 1

    return sections


def format_for_matrix(sections: list[TodoSection]) -> str:
    """Return a compact Matrix-formatted overview of open TODOs."""
    total_open = sum(len(s.open_items) for s in sections)
    total_done = sum(s.done_count for s in sections)

    lines: list[str] = [
        f"📋 **Offene TODOs** — {total_open} offen, {total_done} erledigt",
        "",
    ]

    open_sections = [s for s in sections if s.open_items]
    if not open_sections:
        lines.append("✅ Alles erledigt!")
        return "\n".join(lines)

    for section in open_sections:
        lines.append(
            f"{section.emoji} **{section.priority}** — {len(section.open_items)} offen:"
        )
        for item in section.open_items:
            # Trim to 80 chars so the Matrix message stays readable
            short = item if len(item) <= 80 else item[:77] + "…"
            lines.append(f"  • {short}")
        lines.append("")

    return "\n".join(lines).rstrip()


def get_project_todos(projects: dict) -> dict[str, list[TodoSection]]:
    """Read TODO.md for each project and return name → sections mapping.

    *projects* may be a dict of Project dataclass instances or raw dicts
    — both are accepted as long as they expose ``local_path``.
    Only projects that actually have a non-empty TODO.md are included.
    """
    result: dict[str, list[TodoSection]] = {}
    for name, proj in projects.items():
        local_path = (
            proj.local_path
            if hasattr(proj, "local_path")
            else proj.get("local_path", "")
        )
        if not local_path:
            continue
        sections = parse_todo_file(Path(local_path) / "TODO.md")
        if sections:
            result[name] = sections
    return result


def format_project_summary(project_todos: dict[str, list[TodoSection]]) -> str:
    """Return a compact Matrix summary: open count per project."""
    if not project_todos:
        return "📋 Keine Projekte mit TODO.md gefunden."

    total_open = sum(
        sum(len(s.open_items) for s in secs) for secs in project_todos.values()
    )
    lines: list[str] = [
        f"📋 **Projekt-TODO-Übersicht** — {total_open} offen gesamt",
        "",
    ]
    for name in sorted(project_todos):
        sections = project_todos[name]
        open_count = sum(len(s.open_items) for s in sections)
        done_count = sum(s.done_count for s in sections)
        if open_count == 0:
            lines.append(f"✅ **{name}** — alles erledigt ({done_count} gesamt)")
        else:
            lines.append(f"📌 **{name}** — {open_count} offen, {done_count} erledigt")
    return "\n".join(lines)


def format_project_detail(name: str, sections: list[TodoSection]) -> str:
    """Return a detailed Matrix overview of one project's open TODOs."""
    total_open = sum(len(s.open_items) for s in sections)
    total_done = sum(s.done_count for s in sections)
    lines: list[str] = [
        f"📋 **TODOs: {name}** — {total_open} offen, {total_done} erledigt",
        "",
    ]
    open_sections = [s for s in sections if s.open_items]
    if not open_sections:
        lines.append("✅ Alles erledigt!")
        return "\n".join(lines)

    for section in open_sections:
        lines.append(
            f"{section.emoji} **{section.priority}** — {len(section.open_items)} offen:"
        )
        for item in section.open_items:
            short = item if len(item) <= 80 else item[:77] + "…"
            lines.append(f"  • {short}")
        lines.append("")
    return "\n".join(lines).rstrip()
