"""Loader rules that can be checked without a database."""

import re

import pytest

from kbo.load import DDL_STATEMENTS, UpdateSequenceError, _to_snake_case, check_extract_order

from .builders import HEADERS


def test_the_next_extract_is_accepted():
    check_extract_order(current=540, incoming=541)


@pytest.mark.parametrize(
    "incoming, reason",
    [
        pytest.param(540, "already applied", id="same-extract-again"),
        pytest.param(538, "already applied", id="older-extract"),
        pytest.param(542, "extract 541 must be applied first", id="skips-one"),
        pytest.param(600, "extract 541 must be applied first", id="skips-many"),
    ],
)
def test_any_other_extract_is_refused(incoming, reason):
    with pytest.raises(UpdateSequenceError, match=reason):
        check_extract_order(current=540, incoming=incoming)


def _table_columns() -> dict[str, list[str]]:
    """{table: [column, ...]} as declared in the CREATE TABLE statements."""
    tables = {}
    for name, body in re.findall(r"CREATE TABLE kbo\.(\w+) \((.*?)\n\);", DDL_STATEMENTS, re.S):
        lines = [line.split() for line in body.splitlines() if line.strip()]
        tables[name] = [words[0] for words in lines if words[0] != "PRIMARY"]
    return tables


@pytest.mark.parametrize("table", HEADERS)
def test_table_columns_match_the_csv_headers(table):
    # COPY maps CSV columns to table columns by name, so every header of the real
    # archive layout must have a column of the same (snake_cased) name.
    assert _table_columns()[table] == [_to_snake_case(h) for h in HEADERS[table]]
