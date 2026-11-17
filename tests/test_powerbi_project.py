"""The Power BI projects stay consistent with themselves and with the powerbi views."""

import json
import re
from pathlib import Path

import pytest

from kbo.powerbi import VIEW_NAMES

from .test_warehouse_db import warehouse  # noqa: F401  (fixture)

POWERBI = Path(__file__).parent.parent / "powerbi"
PROJECTS = ["KBO Analysis", "KBO Pipeline Health"]


def _name(raw: str) -> str:
    return raw.strip().strip("'").replace("''", "'")


def model_tables(project: str) -> dict[str, dict]:
    """{table: {"columns": {...}, "measures": {...}, "view": "..."}} read from the TMDL files."""
    tables = {}
    for path in (POWERBI / f"{project}.SemanticModel" / "definition" / "tables").glob("*.tmdl"):
        text = path.read_text(encoding="utf-8")
        table = _name(re.search(r"^table (.+)$", text, re.M).group(1))
        tables[table] = {
            "columns": {_name(m) for m in re.findall(r"^\tcolumn (.+)$", text, re.M)},
            "measures": {_name(m) for m in re.findall(r"^\tmeasure (.+?) =", text, re.M)},
            "view": re.search(r'Item = "([^"]+)"', text).group(1),
        }
    return tables


def relationships(project: str) -> list[tuple[str, str, str, str]]:
    """(from table, from column, to table, to column) for every relationship."""
    path = POWERBI / f"{project}.SemanticModel" / "definition" / "relationships.tmdl"
    if not path.exists():
        return []
    ends = re.findall(r"^\t(?:from|to)Column: ('[^']*(?:''[^']*)*'|[^.']+)\.(.+)$", path.read_text(encoding="utf-8"), re.M)
    ends = [(_name(table), _name(column)) for table, column in ends]
    return [(*ends[i], *ends[i + 1]) for i in range(0, len(ends), 2)]


def visual_fields(project: str):
    """(visual folder, kind, table, name) for every field a visual uses."""
    for path in (POWERBI / f"{project}.Report" / "definition").glob("pages/*/visuals/*/visual.json"):
        found = []

        def walk(node):
            if isinstance(node, dict):
                for kind in ("Column", "Measure"):
                    ref = node.get(kind)
                    if isinstance(ref, dict) and "Property" in ref:
                        found.append((kind, ref["Expression"]["SourceRef"]["Entity"], ref["Property"]))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(json.loads(path.read_text(encoding="utf-8")))
        for kind, table, name in found:
            yield path.parent.name, kind, table, name


@pytest.mark.parametrize("project", PROJECTS)
def test_every_visual_field_exists_in_the_model(project):
    tables = model_tables(project)
    missing = [
        (visual, kind, table, name)
        for visual, kind, table, name in visual_fields(project)
        if name not in tables.get(table, {}).get("columns" if kind == "Column" else "measures", set())
    ]

    assert missing == []


@pytest.mark.parametrize("project", PROJECTS)
def test_every_page_in_the_page_order_exists(project):
    pages_dir = POWERBI / f"{project}.Report" / "definition" / "pages"
    pages = json.loads((pages_dir / "pages.json").read_text(encoding="utf-8"))

    assert {p.name for p in pages_dir.iterdir() if p.is_dir()} == set(pages["pageOrder"])


@pytest.mark.parametrize("project", PROJECTS)
def test_every_table_reads_a_powerbi_view(project):
    assert {t["view"] for t in model_tables(project).values()} <= set(VIEW_NAMES)


def test_the_analysis_model_relates_each_fact_to_its_dimensions():
    tables = model_tables("KBO Analysis")
    links = relationships("KBO Analysis")

    for from_table, from_column, to_table, to_column in links:
        assert from_column in tables[from_table]["columns"]
        assert to_column in tables[to_table]["columns"]
    assert {(f, t) for f, _, t, _ in links} == {
        *(("Enterprise Facts", d) for d in
          ["Enterprise", "Date", "Geography", "Activity", "Juridical Form", "Juridical Situation"]),
        *(("Establishment Facts", d) for d in ["Enterprise", "Date", "Geography", "Activity"]),
    }


@pytest.mark.db
@pytest.mark.parametrize("project", PROJECTS)
def test_model_columns_match_the_powerbi_views(project, warehouse, db):
    for table, content in model_tables(project).items():
        view_columns = set(db.column(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = 'powerbi' AND table_name = '{content['view']}'"
        ))
        assert content["columns"] == view_columns, table
