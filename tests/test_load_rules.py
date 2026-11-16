"""Loader rules that can be checked without a database."""

import re

import pytest

from kbo.load import (
    DDL_STATEMENTS,
    ArchiveMismatchError,
    UpdateSequenceError,
    check_archive_identity,
    check_extract_order,
)

from kbo.transformation import clean_header

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


UPDATE_544 = "KboOpenData_0544_2026_11_14_Update.zip"
FULL_540 = "KboOpenData_0540_2026_11_10_Full.zip"


def meta(extract_type: str, number) -> dict[str, str]:
    return {"ExtractType": extract_type, "ExtractNumber": str(number)}


def test_archive_whose_metadata_matches_its_name_is_accepted():
    assert check_archive_identity(UPDATE_544, "Update", meta("update", 544)) == 544
    assert check_archive_identity(FULL_540, "Full", meta("full", 540)) == 540
    # a file that does not follow the naming scheme is judged on its metadata alone
    assert check_archive_identity("renamed.zip", "Update", meta("update", 7)) == 7


@pytest.mark.parametrize(
    "filename, load_as, metadata, reason",
    [
        pytest.param(UPDATE_544, "Update", meta("update", 545), "meta.csv says extract 545", id="other-extract-inside"),
        pytest.param(UPDATE_544, "Update", meta("full", 544), "ExtractType='full'", id="full-inside-update"),
        pytest.param(FULL_540, "Full", meta("update", 540), "ExtractType='update'", id="update-inside-full"),
        pytest.param(FULL_540, "Update", meta("update", 540), "named as a Full archive", id="full-loaded-as-update"),
        pytest.param(UPDATE_544, "Update", meta("update", "n/a"), "no usable ExtractNumber", id="no-number"),
        pytest.param(UPDATE_544, "Update", {}, "ExtractType=''", id="empty-metadata"),
    ],
)
def test_archive_whose_metadata_contradicts_it_is_refused(filename, load_as, metadata, reason):
    with pytest.raises(ArchiveMismatchError, match=reason):
        check_archive_identity(filename, load_as, metadata)


def _table_columns() -> dict[str, list[str]]:
    """{table: [column, ...]} as declared in the CREATE TABLE statements."""
    tables = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS kbo\.(\w+) \((.*?)\n\);", DDL_STATEMENTS, re.S):
        lines = [line.split() for line in body.splitlines() if line.strip()]
        tables[name] = [words[0] for words in lines if words[0] != "PRIMARY"]
    return tables


@pytest.mark.parametrize("table", HEADERS)
def test_table_columns_match_the_csv_headers(table):
    # COPY maps CSV columns to table columns by name, so every header of the real
    # archive layout must have a column of the same (snake_cased) name.
    assert _table_columns()[table] == clean_header(HEADERS[table])
