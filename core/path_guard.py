"""Path and name validation utilities — prevent traversal and injection."""
from __future__ import annotations

import re
from pathlib import Path

# Safe project name: starts with alphanumeric, allows hyphens/underscores/dots, max 128 chars.
# Explicitly forbids: /, \, .., spaces, shell metacharacters.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class PathGuardError(ValueError):
    """Raised when a path or name fails validation."""


def validate_project_name(name: str) -> str:
    """Ensure *name* is a safe project identifier.

    Raises PathGuardError if the name contains path separators, shell
    metacharacters, or is otherwise unsuitable as a directory/key name.
    Returns the stripped name on success.
    """
    name = name.strip()
    if not name:
        raise PathGuardError("Project name must not be empty.")
    if not _NAME_RE.match(name):
        raise PathGuardError(
            f"Invalid project name {name!r}. "
            "Use only letters, digits, hyphens, underscores, and dots."
        )
    return name


def validate_project_path(path: str, allowed_roots: list[str]) -> str:
    """Ensure *path* resolves to a location inside one of *allowed_roots*.

    Resolves symlinks and ``..`` components before comparison.  Raises
    PathGuardError when the resolved path is outside every allowed root.
    Returns the resolved (canonical) path string on success.

    Note: the path itself does not need to exist yet — only the *allowed_roots*
    must be valid directories.
    """
    path = path.strip()
    if not path:
        raise PathGuardError("Project path must not be empty.")

    # Resolve as far as possible; non-existent tail components are kept as-is
    # by Path.resolve(strict=False) (Python ≥ 3.6).
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise PathGuardError(f"Cannot resolve path {path!r}: {exc}") from exc

    for root in allowed_roots:
        try:
            root_resolved = Path(root).resolve(strict=False)
            resolved.relative_to(root_resolved)  # raises ValueError if not relative
            return str(resolved)
        except ValueError:
            continue

    raise PathGuardError(
        f"Path {path!r} is outside the allowed directories: {allowed_roots}. "
        "Only paths inside these roots are permitted."
    )


def safe_room_id(room_id: str) -> str:
    """Validate a Matrix room ID: must match !localpart:server format.

    Raises PathGuardError on invalid format.  Returns the stripped value.
    """
    room_id = room_id.strip()
    # Basic Matrix room ID pattern: !<opaque>:<server>
    if not re.match(r"^![A-Za-z0-9._~!$&'()*+,;=:@%/-]{1,255}:[A-Za-z0-9._-]{1,255}$", room_id):
        raise PathGuardError(
            f"Invalid Matrix room ID {room_id!r}. Expected format: !localpart:server"
        )
    return room_id
