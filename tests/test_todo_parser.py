"""Tests for core/todo_parser.py."""
from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from core.todo_parser import (
    TodoSection,
    format_for_matrix,
    format_project_detail,
    format_project_summary,
    get_project_todos,
    parse_todo_file,
)

SAMPLE = textwrap.dedent("""\
    # TODO

    ## P0 -- MVP zwingend

    ### 1) Basis
    - [x] Verzeichnisstruktur anlegen
    - [x] Grunddateien anlegen
    - [ ] Start nach Reboot prüfen

    ## P1-SECURITY -- Härtung

    ### 14) devagent-User
    - [ ] devagent-User einrichten
    - [ ] Home-Dir anlegen

    ## P3 -- Zukunft

    ### 19) Multi-Backend
    - [ ] Multi-Backend UI
    - [x] Event Push
""")


def _write(content: str) -> str:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
    tmp.write(content)
    tmp.close()
    return tmp.name


class ParseTodoFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _write(SAMPLE)

    def test_returns_three_sections(self) -> None:
        sections = parse_todo_file(self.path)
        self.assertEqual(len(sections), 3)

    def test_section_priorities(self) -> None:
        sections = parse_todo_file(self.path)
        self.assertEqual(sections[0].priority, "P0")
        self.assertEqual(sections[1].priority, "P1-SECURITY")
        self.assertEqual(sections[2].priority, "P3")

    def test_open_items_counted_correctly(self) -> None:
        sections = parse_todo_file(self.path)
        self.assertEqual(len(sections[0].open_items), 1)
        self.assertEqual(len(sections[1].open_items), 2)
        self.assertEqual(len(sections[2].open_items), 1)

    def test_done_items_counted_correctly(self) -> None:
        sections = parse_todo_file(self.path)
        self.assertEqual(sections[0].done_count, 2)
        self.assertEqual(sections[1].done_count, 0)
        self.assertEqual(sections[2].done_count, 1)

    def test_total_is_open_plus_done(self) -> None:
        sections = parse_todo_file(self.path)
        for s in sections:
            self.assertEqual(s.total, s.done_count + len(s.open_items))

    def test_open_item_text(self) -> None:
        sections = parse_todo_file(self.path)
        self.assertIn("Start nach Reboot prüfen", sections[0].open_items)
        self.assertIn("devagent-User einrichten", sections[1].open_items)

    def test_missing_file_returns_empty(self) -> None:
        sections = parse_todo_file("/nonexistent/todo.md")
        self.assertEqual(sections, [])


class FormatForMatrixTests(unittest.TestCase):
    def _sections(self) -> list[TodoSection]:
        return parse_todo_file(_write(SAMPLE))

    def test_contains_header_counts(self) -> None:
        text = format_for_matrix(self._sections())
        self.assertIn("4 offen", text)
        self.assertIn("3 erledigt", text)

    def test_contains_priority_labels(self) -> None:
        text = format_for_matrix(self._sections())
        self.assertIn("P0", text)
        self.assertIn("P1-SECURITY", text)
        self.assertIn("P3", text)

    def test_contains_open_item_text(self) -> None:
        text = format_for_matrix(self._sections())
        self.assertIn("Start nach Reboot prüfen", text)
        self.assertIn("devagent-User einrichten", text)

    def test_long_item_trimmed_to_80(self) -> None:
        long_item = "x" * 100
        sections = [TodoSection(priority="P0", title="Test", open_items=[long_item])]
        text = format_for_matrix(sections)
        for line in text.splitlines():
            if "•" in line:
                # strip the "  • " prefix (4 chars)
                item_part = line.strip().lstrip("• ").strip()
                self.assertLessEqual(len(item_part), 83)  # 80 + possible "…"

    def test_all_done_returns_checkmark_message(self) -> None:
        sections = [TodoSection(priority="P0", title="Basis", open_items=[], done_count=5)]
        text = format_for_matrix(sections)
        self.assertIn("Alles erledigt", text)

    def test_empty_sections_list(self) -> None:
        text = format_for_matrix([])
        self.assertIn("Alles erledigt", text)


class GetProjectTodosTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        # Create two fake projects with TODO.md
        for proj_name in ("alpha", "beta"):
            proj_dir = Path(self.tmp.name) / proj_name
            proj_dir.mkdir()
            (proj_dir / "TODO.md").write_text(SAMPLE, encoding="utf-8")
        # gamma has no TODO.md
        (Path(self.tmp.name) / "gamma").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _projects_dict(self) -> dict:
        return {
            "alpha": {"local_path": str(Path(self.tmp.name) / "alpha")},
            "beta":  {"local_path": str(Path(self.tmp.name) / "beta")},
            "gamma": {"local_path": str(Path(self.tmp.name) / "gamma")},
        }

    def test_only_projects_with_todo_included(self) -> None:
        result = get_project_todos(self._projects_dict())
        self.assertIn("alpha", result)
        self.assertIn("beta", result)
        self.assertNotIn("gamma", result)

    def test_sections_parsed_correctly(self) -> None:
        result = get_project_todos(self._projects_dict())
        self.assertEqual(len(result["alpha"]), 3)

    def test_missing_local_path_skipped(self) -> None:
        projects = {"empty": {"local_path": ""}}
        result = get_project_todos(projects)
        self.assertEqual(result, {})

    def test_accepts_dataclass_like_objects(self) -> None:
        """get_project_todos should work with objects having local_path attribute."""
        class FakeProj:
            def __init__(self, path: str) -> None:
                self.local_path = path

        projects = {
            "alpha": FakeProj(str(Path(self.tmp.name) / "alpha")),
            "gamma": FakeProj(str(Path(self.tmp.name) / "gamma")),
        }
        result = get_project_todos(projects)
        self.assertIn("alpha", result)
        self.assertNotIn("gamma", result)


class FormatProjectSummaryTests(unittest.TestCase):
    def _make_todos(self) -> dict:
        sections = parse_todo_file(_write(SAMPLE))
        return {"alpha": sections, "beta": [TodoSection(priority="P0", title="Done", open_items=[], done_count=3)]}

    def test_shows_project_names(self) -> None:
        text = format_project_summary(self._make_todos())
        self.assertIn("alpha", text)
        self.assertIn("beta", text)

    def test_shows_open_counts(self) -> None:
        text = format_project_summary(self._make_todos())
        # alpha has 4 open items per SAMPLE
        self.assertIn("4 offen", text)

    def test_all_done_project_shows_checkmark(self) -> None:
        text = format_project_summary(self._make_todos())
        self.assertIn("alles erledigt", text)

    def test_empty_dict_returns_fallback(self) -> None:
        text = format_project_summary({})
        self.assertIn("gefunden", text)


class FormatProjectDetailTests(unittest.TestCase):
    def test_shows_project_name_in_header(self) -> None:
        sections = parse_todo_file(_write(SAMPLE))
        text = format_project_detail("MyProject", sections)
        self.assertIn("MyProject", text)

    def test_shows_open_count(self) -> None:
        sections = parse_todo_file(_write(SAMPLE))
        text = format_project_detail("MyProject", sections)
        self.assertIn("4 offen", text)

    def test_all_done_shows_checkmark(self) -> None:
        sections = [TodoSection(priority="P0", title="Done", open_items=[], done_count=2)]
        text = format_project_detail("MyProject", sections)
        self.assertIn("Alles erledigt", text)


if __name__ == "__main__":
    unittest.main()
