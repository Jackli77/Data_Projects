"""Loading archives into PostgreSQL. Needs a server; run without it via `-m "not db"`."""

from datetime import date

import psycopg
import pytest

from kbo.load import (
    DB_CONFIG,
    ArchiveMismatchError,
    UpdateSequenceError,
    find_orphans,
    load_archive,
    load_kbo_to_postgres,
)

from .builders import (
    ENT_A,
    ENT_B,
    ENT_C,
    FULL_DATE,
    FULL_NUMBER,
    NEXT_DATE,
    meta_csv,
    to_csv,
    write_full,
    write_update,
)

pytestmark = pytest.mark.db


def load(data_dir, day: date, archive_type: str = "Update") -> None:
    load_kbo_to_postgres(day, archive_type=archive_type, data_dir=data_dir)


@pytest.fixture
def data_dir(db, tmp_path):
    """Folder for archives, with a database that already holds the seed Full load
    (enterprises A and B, extract 540)."""
    write_full(tmp_path)
    load(tmp_path, FULL_DATE, "Full")
    return tmp_path


# --- Full ------------------------------------------------------------------------------


def test_full_load_fills_every_table(data_dir, db):
    assert db.counts() == {
        "activity": 2,
        "address": 2,
        "branch": 0,
        "code": 2,
        "contact": 1,
        "denomination": 2,
        "enterprise": 2,
        "establishment": 1,
        "meta": 3,
    }
    assert db.extract_number == FULL_NUMBER
    # dates arrive as DD-MM-YYYY
    assert db.query(f"SELECT start_date FROM kbo.enterprise WHERE enterprise_number = '{ENT_A}'") == [
        (date(1960, 8, 9),)
    ]


def test_full_load_replaces_whatever_was_there(data_dir, db):
    write_update(data_dir, inserts={"enterprise": [{"EnterpriseNumber": ENT_C}]})
    load(data_dir, NEXT_DATE)

    load(data_dir, FULL_DATE, "Full")

    assert db.column("SELECT enterprise_number FROM kbo.enterprise ORDER BY 1") == [ENT_A, ENT_B]
    assert db.extract_number == FULL_NUMBER


def test_full_load_keeps_views_built_on_the_tables(data_dir, db):
    db.execute("CREATE SCHEMA reporting")
    db.execute("CREATE VIEW reporting.active AS SELECT * FROM kbo.enterprise WHERE status = 'AC'")

    load(data_dir, FULL_DATE, "Full")

    assert db.column("SELECT count(*) FROM reporting.active") == [2]


# --- Update ----------------------------------------------------------------------------


def test_update_applies_deletes_and_inserts(data_dir, db):
    # A moves (its address is deleted and re-inserted), B disappears, C is new.
    write_update(
        data_dir,
        deletes={
            "enterprise": [ENT_B],
            "denomination": [ENT_B],
            "address": [ENT_A, ENT_B],
            "activity": [ENT_B],
        },
        inserts={
            "enterprise": [{"EnterpriseNumber": ENT_C}],
            "address": [
                {"EntityNumber": ENT_A, "StreetNL": "New Street"},
                {"EntityNumber": ENT_C, "StreetNL": "Wetstraat"},
            ],
        },
    )

    load(data_dir, NEXT_DATE)

    assert db.column("SELECT enterprise_number FROM kbo.enterprise ORDER BY 1") == [ENT_A, ENT_C]
    assert db.query("SELECT entity_number, street_nl FROM kbo.address ORDER BY 1") == [
        (ENT_A, "New Street"),
        (ENT_C, "Wetstraat"),
    ]
    assert db.column("SELECT entity_number FROM kbo.activity") == [ENT_A]
    assert db.extract_number == FULL_NUMBER + 1


def test_update_replaces_the_code_table(data_dir, db):
    # Updates ship code.csv complete rather than as delete/insert files.
    write_update(
        data_dir,
        code=[
            {"Category": "Status", "Code": "AC", "Language": "NL", "Description": "Actief (nieuw)"},
            {"Category": "Status", "Code": "ST", "Language": "NL", "Description": "Stopgezet"},
        ],
    )

    load(data_dir, NEXT_DATE)

    assert db.query("SELECT code, language, description FROM kbo.code ORDER BY 1") == [
        ("AC", "NL", "Actief (nieuw)"),
        ("ST", "NL", "Stopgezet"),
    ]


def test_two_updates_of_one_date_apply_in_extract_order(data_dir, db):
    email = {"EntityNumber": ENT_B, "ContactType": "EMAIL", "Value": "info@b.example"}
    # Written newest first: 541 adds B's e-mail, 542 removes B's contacts again.
    write_update(data_dir, FULL_NUMBER + 2, deletes={"contact": [ENT_B]})
    write_update(data_dir, FULL_NUMBER + 1, inserts={"contact": [email]})

    load(data_dir, NEXT_DATE)

    assert db.column("SELECT entity_number FROM kbo.contact") == [ENT_A]
    assert db.extract_number == FULL_NUMBER + 2


def test_csv_columns_are_matched_by_name_not_position(data_dir, db):
    reordered = to_csv(
        ["EnterpriseNumber", "Id", "StartDate"],
        [{"EnterpriseNumber": ENT_A, "Id": "9.000.000.001", "StartDate": "02-03-2021"}],
    )
    write_update(data_dir, files={"branch_insert.csv": reordered})

    load(data_dir, NEXT_DATE)

    assert db.query("SELECT id, start_date, enterprise_number FROM kbo.branch") == [
        ("9.000.000.001", date(2021, 3, 2), ENT_A)
    ]


# --- Updates that must be refused, leaving the database as it was -------------------------


@pytest.fixture
def unchanged(data_dir, db):
    """Asserts after the test that the database still equals the seed Full load."""
    before = db.counts()
    yield
    assert db.counts() == before
    assert db.extract_number == FULL_NUMBER


def test_skipping_an_update_is_refused(data_dir, unchanged):
    write_update(data_dir, FULL_NUMBER + 2, deletes={"enterprise": [ENT_A]})

    with pytest.raises(UpdateSequenceError, match="must be applied first"):
        load(data_dir, NEXT_DATE)


def test_update_with_an_unknown_column_is_refused(data_dir, unchanged):
    surprise = to_csv(["EntityNumber", "Surprise"], [{"EntityNumber": ENT_A, "Surprise": "x"}])
    # The enterprise insert comes first and must be rolled back with the rest.
    write_update(
        data_dir,
        inserts={"enterprise": [{"EnterpriseNumber": ENT_C}]},
        files={"contact_insert.csv": surprise},
    )

    with pytest.raises(psycopg.errors.UndefinedColumn):
        load(data_dir, NEXT_DATE)


def test_archive_whose_metadata_contradicts_its_name_is_refused(data_dir, unchanged):
    # Named extract 541, but the contents say 542.
    write_update(
        data_dir,
        deletes={"enterprise": [ENT_A]},
        files={"meta.csv": meta_csv("update", FULL_NUMBER + 2)},
    )

    with pytest.raises(ArchiveMismatchError, match="meta.csv says extract 542"):
        load(data_dir, NEXT_DATE)


def test_loading_the_same_archive_twice_is_refused(data_dir, db):
    # contact has no primary key, so nothing but the sequence check stops a duplicate.
    email = {"EntityNumber": ENT_B, "ContactType": "EMAIL", "Value": "info@b.example"}
    archive = write_update(data_dir, inserts={"contact": [email]})
    load_archive(archive)

    with pytest.raises(UpdateSequenceError, match="already applied"):
        load_archive(archive)

    assert db.column("SELECT count(*) FROM kbo.contact") == [2]


def test_update_before_any_full_load_is_refused(db, tmp_path):
    write_update(tmp_path)

    with pytest.raises(UpdateSequenceError, match="run a Full load"):
        load(tmp_path, NEXT_DATE)


# --- Repeating and recovering ------------------------------------------------------------


def test_repeating_a_finished_date_changes_nothing(data_dir, db):
    email = {"EntityNumber": ENT_B, "ContactType": "EMAIL", "Value": "info@b.example"}
    write_update(data_dir, inserts={"contact": [email]})
    load(data_dir, NEXT_DATE)

    load(data_dir, NEXT_DATE)  # skipped: already applied

    assert db.column("SELECT count(*) FROM kbo.contact") == [2]
    assert db.extract_number == FULL_NUMBER + 1


def test_rerun_after_a_partial_failure_applies_only_what_is_missing(data_dir, db):
    # Two updates on one date: 541 is fine, 542 is broken.
    email = {"EntityNumber": ENT_B, "ContactType": "EMAIL", "Value": "info@b.example"}
    surprise = to_csv(["EntityNumber", "Surprise"], [{"EntityNumber": ENT_A, "Surprise": "x"}])
    write_update(data_dir, FULL_NUMBER + 1, inserts={"contact": [email]})
    write_update(data_dir, FULL_NUMBER + 2, files={"contact_insert.csv": surprise})

    with pytest.raises(psycopg.errors.UndefinedColumn):
        load(data_dir, NEXT_DATE)
    assert db.extract_number == FULL_NUMBER + 1  # 541 went in, 542 did not

    # The archive is replaced by a good one and the same command is run again.
    write_update(data_dir, FULL_NUMBER + 2, inserts={"enterprise": [{"EnterpriseNumber": ENT_C}]})
    load(data_dir, NEXT_DATE)

    assert db.extract_number == FULL_NUMBER + 2
    assert db.column("SELECT count(*) FROM kbo.contact") == [2]  # 541 was not applied twice
    assert ENT_C in db.column("SELECT enterprise_number FROM kbo.enterprise")


# --- Run history and integrity -------------------------------------------------------------


def test_every_run_is_recorded_in_the_history(data_dir, db):
    email = {"EntityNumber": ENT_B, "ContactType": "EMAIL", "Value": "info@b.example"}
    write_update(data_dir, FULL_NUMBER + 1, deletes={"contact": [ENT_A]}, inserts={"contact": [email]})
    write_update(data_dir, FULL_NUMBER + 3, day=date(2026, 11, 13))  # skips 542

    load(data_dir, NEXT_DATE)
    with pytest.raises(UpdateSequenceError):
        load(data_dir, date(2026, 11, 13))

    assert db.history() == [
        (540, "success", None, 540),
        (541, "success", 540, 541),
        (543, "failed", 541, None),
    ]
    assert db.query(
        "SELECT rows_inserted, rows_deleted, error FROM kbo.load_history WHERE extract_number = 541"
    ) == [(1, 1, None)]
    error, tb = db.query(
        "SELECT error, traceback FROM kbo.load_history WHERE status = 'failed'"
    )[0]
    assert error.startswith("UpdateSequenceError: Update 543 is out of sequence")
    assert "Traceback" in tb


def test_history_survives_a_full_load(data_dir, db):
    load(data_dir, FULL_DATE, "Full")

    assert [status for _, status, _, _ in db.history()] == ["success", "success"]


def test_orphaned_establishments_are_counted(data_dir, db):
    write_update(
        data_dir,
        inserts={"establishment": [{"EstablishmentNumber": "2.999.999.999", "EnterpriseNumber": "0999.999.999"}]},
    )
    load(data_dir, NEXT_DATE)

    with psycopg.connect(**DB_CONFIG) as conn:
        assert find_orphans(conn) == {
            "establishments without an enterprise": 1,
            "branches without an enterprise": 0,
        }
