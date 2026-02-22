"""Tests for ui/projects_registry.py"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ui.projects_registry import Project, ProjectRegistry, scan_local_projects


# ── ProjectRegistry: load / save / upsert / remove ───────────────────────────

class RegistryPersistenceTests(unittest.TestCase):
    def test_load_nonexistent_file_returns_empty_registry(self) -> None:
        reg = ProjectRegistry.load("/nonexistent/path/projects.json")
        self.assertEqual(reg.projects, {})

    def test_upsert_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/projects.json"
            reg = ProjectRegistry.load(path)
            reg.upsert(Project(name="myapp", local_path="/home/dev/myapp"))

            reg2 = ProjectRegistry.load(path)
            self.assertIn("myapp", reg2.projects)
            self.assertEqual(reg2.projects["myapp"].local_path, "/home/dev/myapp")

    def test_upsert_overwrites_existing_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/p.json"
            reg = ProjectRegistry.load(path)
            reg.upsert(Project(name="app", local_path="/old/path"))
            reg.upsert(Project(name="app", local_path="/new/path"))

            reg2 = ProjectRegistry.load(path)
            self.assertEqual(reg2.projects["app"].local_path, "/new/path")

    def test_remove_deletes_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/p.json"
            reg = ProjectRegistry.load(path)
            reg.upsert(Project(name="app", local_path="/x"))
            reg.remove("app")

            reg2 = ProjectRegistry.load(path)
            self.assertNotIn("app", reg2.projects)

    def test_remove_nonexistent_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.remove("nonexistent")  # must not raise

    def test_save_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/deep/nested/projects.json"
            reg = ProjectRegistry.load(path)
            reg.upsert(Project(name="x", local_path="/x"))
            self.assertTrue(Path(path).exists())

    def test_saved_json_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/p.json"
            reg = ProjectRegistry.load(path)
            reg.upsert(Project(name="a", local_path="/a", matrix_room_id="!r:m.org"))
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertIn("projects", data)
            self.assertIn("a", data["projects"])

    def test_multiple_projects_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/p.json"
            reg = ProjectRegistry.load(path)
            for name in ["alpha", "beta", "gamma"]:
                reg.upsert(Project(name=name, local_path=f"/{name}"))

            reg2 = ProjectRegistry.load(path)
            self.assertEqual(set(reg2.projects.keys()), {"alpha", "beta", "gamma"})

    def test_is_initialized_false_without_room_id(self) -> None:
        proj = Project(name="x", local_path="/x")
        self.assertFalse(proj.is_initialized())

    def test_is_initialized_true_with_room_id(self) -> None:
        proj = Project(name="x", local_path="/x", matrix_room_id="!r:m.org")
        self.assertTrue(proj.is_initialized())


# ── room_to_project ───────────────────────────────────────────────────────────

class RoomToProjectTests(unittest.TestCase):
    def _registry(self) -> ProjectRegistry:
        with tempfile.TemporaryDirectory() as tmp:
            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.upsert(Project(name="foo", local_path="/foo", matrix_room_id="!abc:matrix.org"))
            reg.upsert(Project(name="bar", local_path="/bar", matrix_room_id="!def:matrix.org"))
            reg.upsert(Project(name="noroom", local_path="/noroom"))
            return reg

    def test_returns_correct_project(self) -> None:
        reg = self._registry()
        proj = reg.room_to_project("!abc:matrix.org")
        self.assertIsNotNone(proj)
        self.assertEqual(proj.name, "foo")  # type: ignore[union-attr]

    def test_returns_none_for_unknown_room(self) -> None:
        reg = self._registry()
        self.assertIsNone(reg.room_to_project("!zzz:matrix.org"))

    def test_returns_none_for_project_without_room(self) -> None:
        reg = self._registry()
        self.assertIsNone(reg.room_to_project(""))

    def test_different_rooms_return_different_projects(self) -> None:
        reg = self._registry()
        foo = reg.room_to_project("!abc:matrix.org")
        bar = reg.room_to_project("!def:matrix.org")
        self.assertNotEqual(foo.name, bar.name)  # type: ignore[union-attr]


# ── ensure_claude_md ──────────────────────────────────────────────────────────

class EnsureClaudeMdTests(unittest.TestCase):
    def test_creates_claude_md_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = Path(tmp) / "myapp"
            proj_dir.mkdir()
            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.upsert(Project(name="myapp", local_path=str(proj_dir)))

            created = reg.ensure_claude_md("myapp")

            self.assertTrue(created)
            self.assertTrue((proj_dir / "CLAUDE.md").exists())

    def test_does_not_overwrite_existing_claude_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = Path(tmp) / "myapp"
            proj_dir.mkdir()
            existing = proj_dir / "CLAUDE.md"
            existing.write_text("# custom content", encoding="utf-8")

            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.upsert(Project(name="myapp", local_path=str(proj_dir)))

            created = reg.ensure_claude_md("myapp")

            self.assertFalse(created)
            self.assertEqual(existing.read_text(encoding="utf-8"), "# custom content")

    def test_returns_false_for_unknown_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = ProjectRegistry.load(f"{tmp}/p.json")
            self.assertFalse(reg.ensure_claude_md("nobody"))

    def test_includes_project_name_in_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = Path(tmp) / "coolproject"
            proj_dir.mkdir()
            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.upsert(Project(name="coolproject", local_path=str(proj_dir)))
            reg.ensure_claude_md("coolproject")

            content = (proj_dir / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("coolproject", content)

    def test_includes_room_id_when_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = Path(tmp) / "app"
            proj_dir.mkdir()
            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.upsert(Project(name="app", local_path=str(proj_dir),
                               matrix_room_id="!testroom:matrix.org"))
            reg.ensure_claude_md("app")

            content = (proj_dir / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("!testroom:matrix.org", content)

    def test_returns_false_when_local_path_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.projects["ghost"] = Project(name="ghost", local_path="")
            self.assertFalse(reg.ensure_claude_md("ghost"))


# ── setup_repo_symlink ────────────────────────────────────────────────────────

class SetupRepoSymlinkTests(unittest.TestCase):
    def test_creates_symlink_to_project_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = Path(tmp) / "myapp"
            proj_dir.mkdir()
            repos = Path(tmp) / "repos"

            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.upsert(Project(name="myapp", local_path=str(proj_dir)))

            created = reg.setup_repo_symlink("myapp", str(repos))

            self.assertTrue(created)
            link = repos / "myapp"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), proj_dir.resolve())

    def test_does_not_overwrite_existing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = Path(tmp) / "myapp"
            proj_dir.mkdir()
            repos = Path(tmp) / "repos"
            repos.mkdir()
            link = repos / "myapp"
            link.symlink_to(proj_dir)

            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.upsert(Project(name="myapp", local_path=str(proj_dir)))

            created = reg.setup_repo_symlink("myapp", str(repos))
            self.assertFalse(created)

    def test_returns_false_for_unknown_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = ProjectRegistry.load(f"{tmp}/p.json")
            self.assertFalse(reg.setup_repo_symlink("nobody", tmp))

    def test_creates_repos_root_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = Path(tmp) / "app"
            proj_dir.mkdir()
            repos = Path(tmp) / "deep" / "repos"  # does not exist yet

            reg = ProjectRegistry.load(f"{tmp}/p.json")
            reg.upsert(Project(name="app", local_path=str(proj_dir)))

            created = reg.setup_repo_symlink("app", str(repos))
            self.assertTrue(created)
            self.assertTrue((repos / "app").is_symlink())


# ── scan_local_projects ───────────────────────────────────────────────────────

class ScanLocalProjectsTests(unittest.TestCase):
    def test_finds_git_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ["alpha", "beta"]:
                d = root / name
                d.mkdir()
                (d / ".git").mkdir()

            found = scan_local_projects(tmp)
            names = [p["name"] for p in found]
            self.assertIn("alpha", names)
            self.assertIn("beta", names)

    def test_ignores_non_git_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notarepo").mkdir()
            (root / "alsono").mkdir()

            found = scan_local_projects(tmp)
            self.assertEqual(found, [])

    def test_ignores_files_at_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "README.md").write_text("x")
            found = scan_local_projects(tmp)
            self.assertEqual(found, [])

    def test_nonexistent_root_returns_empty(self) -> None:
        found = scan_local_projects("/nonexistent/dev/root")
        self.assertEqual(found, [])

    def test_result_contains_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            (repo / ".git").mkdir()

            found = scan_local_projects(tmp)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["local_path"], str(repo))

    def test_results_are_sorted_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["zebra", "apple", "mango"]:
                d = Path(tmp) / name
                d.mkdir()
                (d / ".git").mkdir()

            found = scan_local_projects(tmp)
            names = [p["name"] for p in found]
            self.assertEqual(names, sorted(names))

    def test_mixed_git_and_non_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git_repo = Path(tmp) / "hasGit"
            git_repo.mkdir()
            (git_repo / ".git").mkdir()
            (Path(tmp) / "noGit").mkdir()

            found = scan_local_projects(tmp)
            names = [p["name"] for p in found]
            self.assertIn("hasGit", names)
            self.assertNotIn("noGit", names)


if __name__ == "__main__":
    unittest.main()
