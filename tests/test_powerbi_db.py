"""The Power BI layer: readable views over the star schema and a read-only login. Needs PostgreSQL."""

import uuid

import psycopg
import pytest

from kbo import load
from kbo.load import load_kbo_to_postgres
from kbo.powerbi import VIEW_NAMES, grant_reader

from .builders import FULL_NUMBER, NEXT_DATE, write_update
from .test_warehouse_db import COMPANY, NEWCOMER, warehouse  # noqa: F401  (fixture)

pytestmark = pytest.mark.db


def test_the_schema_holds_exactly_the_listed_views(warehouse, db):
    views = db.column("SELECT table_name FROM information_schema.views WHERE table_schema = 'powerbi'")

    assert sorted(views) == sorted(VIEW_NAMES)


def test_every_view_can_be_queried(warehouse, db):
    for view in VIEW_NAMES:
        db.query(f'SELECT * FROM powerbi."{view}" LIMIT 1')


def test_views_have_the_rows_of_the_star(warehouse, db):
    assert db.column('SELECT count(*) FROM powerbi."Enterprise Facts"') == [2]
    assert db.column('SELECT count(*) FROM powerbi."Establishment Facts"') == [1]


def test_relationship_keys_have_the_same_name_on_both_sides(warehouse, db):
    region = db.column(
        """
        SELECT g."Region"
        FROM powerbi."Enterprise Facts" f
        JOIN powerbi."Enterprise" e USING ("Enterprise Key")
        JOIN powerbi."Geography" g USING ("Geography Key")
        JOIN powerbi."Activity" USING ("Activity Key")
        JOIN powerbi."Date" USING ("Date Key")
        JOIN powerbi."Juridical Form" USING ("Juridical Form Key")
        JOIN powerbi."Juridical Situation" USING ("Juridical Situation Key")
        """
        f"WHERE e.\"Enterprise Number\" = '{COMPANY}'"
    )
    assert region == ["Flanders"]


def test_refresh_view_shows_the_latest_batch(warehouse, db):
    assert db.column('SELECT "Extract Number" FROM powerbi."Refresh"') == [540]


@pytest.fixture
def reader(warehouse, db):
    """A fresh read-only login (roles are shared by the whole server: dropped afterwards)."""
    role, password = f"pbi_test_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    with psycopg.connect(**load.DB_CONFIG, autocommit=True) as conn:
        grant_reader(conn, role, password)
    try:
        with psycopg.connect(**{**load.DB_CONFIG, "user": role, "password": password}, autocommit=True) as conn:
            yield conn
    finally:
        db.execute(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')


def test_reader_sees_the_powerbi_views(reader):
    assert reader.execute('SELECT count(*) FROM powerbi."Enterprise Facts"').fetchone() == (2,)


@pytest.mark.parametrize("table", ["dwh.fact_enterprise", "kbo.enterprise"])
def test_reader_cannot_read_the_layers_below(reader, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute(f"SELECT 1 FROM {table}")


def test_reader_cannot_change_data(reader):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute('DELETE FROM powerbi."Enterprise"')


def test_granting_twice_updates_the_login(warehouse, db):
    role = f"pbi_test_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(**load.DB_CONFIG, autocommit=True) as conn:
            grant_reader(conn, role, "first")
            grant_reader(conn, role, "second")
        with psycopg.connect(**{**load.DB_CONFIG, "user": role, "password": "second"}) as conn:
            assert conn.execute("SELECT current_user").fetchone() == (role,)
    finally:
        db.execute(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')


# --- pipeline monitoring ------------------------------------------------------------------

START_OF_RUN = """SELECT to_char("Started At", 'DD/MM HH24:MI') FROM powerbi."Load Runs" WHERE "Run Order" = {}"""


def check_results(db) -> dict[str, tuple[str, int]]:
    rows = db.query('SELECT "Check", "Status", "Failing Rows" FROM powerbi."Quality Checks"')
    return {check: (status, failing) for check, status, failing in rows}


def test_a_healthy_pipeline_passes_every_error_check(warehouse, db):
    errors = db.column('SELECT "Status" FROM powerbi."Quality Checks" WHERE "Severity" = \'Error\'')

    assert len(errors) == 7
    assert set(errors) == {"Pass"}


def test_a_warehouse_behind_the_database_is_reported(warehouse, db):
    write_update(warehouse, inserts={"enterprise": [{"EnterpriseNumber": NEWCOMER, "TypeOfEnterprise": "1"}]})
    load_kbo_to_postgres(NEXT_DATE, archive_type="Update", data_dir=warehouse)  # no warehouse refresh

    checks = check_results(db)
    assert checks["Warehouse not built from the current extract"] == ("Fail", 1)
    assert checks["Enterprises: source vs warehouse (difference)"] == ("Fail", 1)
    assert checks["Updates missing since the last Full load"] == ("Pass", 0)


def test_load_runs_and_row_counts_reflect_the_loads(warehouse, db):
    write_update(warehouse)
    load_kbo_to_postgres(NEXT_DATE, archive_type="Update", data_dir=warehouse)

    assert db.query('SELECT "Run", "Status" FROM powerbi."Load Runs" ORDER BY "Run Order"') == [
        (f"#1 {FULL_NUMBER} Full ({db.column(START_OF_RUN.format(1))[0]})", "Success"),
        (f"#2 {FULL_NUMBER + 1} Update ({db.column(START_OF_RUN.format(2))[0]})", "Success"),
    ]
    counts = dict(db.query('SELECT "Table", "Rows" FROM powerbi."Table Row Counts"'))
    assert counts["enterprise"] == counts["fact_enterprise"] == 2
