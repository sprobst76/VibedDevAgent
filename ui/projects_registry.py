"""Project registry: persists project → Matrix room mappings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.path_guard import validate_project_name, validate_project_path, PathGuardError  # noqa: F401 (re-export)


@dataclass
class Project:
    name: str
    local_path: str
    matrix_room_id: str = ""
    matrix_room_name: str = ""
    repo_name: str = ""
    created_at: str = ""
    active: bool = True

    def is_initialized(self) -> bool:
        return bool(self.matrix_room_id)


@dataclass
class ProjectRegistry:
    _path: Path
    projects: dict[str, Project] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "ProjectRegistry":
        p = Path(path)
        reg = cls(_path=p)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            for name, raw in data.get("projects", {}).items():
                reg.projects[name] = Project(**{k: v for k, v in raw.items() if k in Project.__dataclass_fields__})
        return reg

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"projects": {n: asdict(p) for n, p in self.projects.items()}}
        self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def upsert(self, project: Project, allowed_roots: list[str] | None = None) -> None:
        """Persist a project after validating its name and path.

        Raises PathGuardError if the name or path is unsafe.
        *allowed_roots* restricts where project paths may live; pass an empty
        list to skip path validation (e.g. during tests with artificial paths).
        """
        validate_project_name(project.name)
        if allowed_roots is not None and len(allowed_roots) > 0:
            project.local_path = validate_project_path(project.local_path, allowed_roots)
        self.projects[project.name] = project
        self.save()

    def remove(self, name: str) -> None:
        self.projects.pop(name, None)
        self.save()


    def room_to_project(self, room_id: str) -> Project | None:
        """Return the project registered for a given Matrix room_id."""
        if not room_id:
            return None
        for proj in self.projects.values():
            if proj.matrix_room_id == room_id:
                return proj
        return None

    def ensure_claude_md(self, name: str) -> bool:
        """Create CLAUDE.md in the project's local_path if it doesn't exist yet.
        Returns True if a new file was created."""
        proj = self.projects.get(name)
        if not proj or not proj.local_path:
            return False
        path = Path(proj.local_path) / "CLAUDE.md"
        if path.exists():
            return False
        try:
            room_line = f"- Matrix-Raum: {proj.matrix_room_id}" if proj.matrix_room_id else ""
            path.write_text(
                f"# {name}\n\n"
                f"## Projekt-Kontext\n"
                f"Dieses Projekt wird über DevAgent via Matrix gesteuert.\n"
                f"{room_line}\n\n"
                f"## Hinweise für den AI-Agenten\n"
                f"- Antworte auf Deutsch wenn der User auf Deutsch schreibt\n"
                f"- Halte Änderungen klein und fokussiert\n"
                f"- Committe nur wenn explizit darum gebeten\n\n"
                f"## Projekt-spezifische Informationen\n"
                f"<!-- Hier projektspezifische Infos eintragen -->\n",
                encoding="utf-8",
            )
            return True
        except Exception:
            return False

    def setup_repo_symlink(self, name: str, repos_root: str) -> bool:
        """Create symlink repos_root/name → project.local_path. Returns True if created."""
        proj = self.projects.get(name)
        if not proj or not proj.local_path:
            return False
        src = Path(proj.local_path)
        dst = Path(repos_root) / name
        if dst.exists() or dst.is_symlink():
            return False  # already exists
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src)
            return True
        except Exception:
            return False


def scan_local_projects(development_root: str) -> list[dict]:
    """Scan development_root for git repos, return list of project info dicts."""
    root = Path(development_root)
    found = []
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / ".git").exists():
            found.append({"name": entry.name, "local_path": str(entry)})
    return found
