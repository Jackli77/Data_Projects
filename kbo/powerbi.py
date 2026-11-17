"""The Power BI layer: the `powerbi` schema and a read-only login.

Power BI reads only the views defined here, never kbo.* or dwh.* directly:

    BUSINESS_VIEWS    the star schema (dwh.*) with readable names: "Enterprise Facts",
                      "Geography", ... Read by powerbi/KBO Analysis.pbip.
    MONITORING_VIEWS  load runs, warehouse refreshes, row counts and quality checks, from
                      kbo.* and dwh.*. Read by powerbi/KBO Pipeline Health.pbip.

The views are the contract with the reports: the warehouse can change underneath as
long as the views keep their columns. Key columns carry the same name on both sides
of a relationship ("Geography Key" in the facts and in Geography), so Power BI can
detect the relationships by itself.

`kbo-warehouse` calls create_views() inside its refresh transaction, then
grant_reader_from_env(). The reader role can query these views and nothing else: the
views run with their owner's rights, so it needs no grant on the tables underneath.
"""

import os

import psycopg
from psycopg import sql

from kbo import load
from kbo.runlog import logger as log

SCHEMA = "powerbi"

BUSINESS_VIEWS = """
CREATE SCHEMA IF NOT EXISTS powerbi;

CREATE OR REPLACE VIEW powerbi."Date" AS
SELECT date_key AS "Date Key", full_date AS "Date", year AS "Year", quarter AS "Quarter",
       month AS "Month", month_name AS "Month Name", day AS "Day",
       iso_weekday AS "Weekday Number", weekday_name AS "Weekday", decade AS "Decade"
FROM dwh.dim_date;

CREATE OR REPLACE VIEW powerbi."Geography" AS
SELECT geography_key AS "Geography Key", zipcode AS "Zipcode",
       municipality_nl AS "Municipality NL", municipality_fr AS "Municipality FR",
       country_nl AS "Country NL", country_fr AS "Country FR",
       province AS "Province", region AS "Region", is_belgium AS "Is Belgium"
FROM dwh.dim_geography;

CREATE OR REPLACE VIEW powerbi."Activity" AS
SELECT activity_key AS "Activity Key", nace_code AS "NACE Code",
       label_nl AS "Activity NL", label_fr AS "Activity FR",
       class_code AS "Class Code", class_label_nl AS "Class NL", class_label_fr AS "Class FR",
       group_code AS "Group Code", group_label_nl AS "Group NL", group_label_fr AS "Group FR",
       division_code AS "Division Code", division_label_nl AS "Division NL",
       division_label_fr AS "Division FR"
FROM dwh.dim_activity;

CREATE OR REPLACE VIEW powerbi."Juridical Form" AS
SELECT juridical_form_key AS "Juridical Form Key", juridical_form_code AS "Juridical Form Code",
       label_nl AS "Juridical Form NL", label_fr AS "Juridical Form FR"
FROM dwh.dim_juridical_form;

CREATE OR REPLACE VIEW powerbi."Juridical Situation" AS
SELECT juridical_situation_key AS "Juridical Situation Key",
       juridical_situation_code AS "Juridical Situation Code",
       label_nl AS "Juridical Situation NL", label_fr AS "Juridical Situation FR"
FROM dwh.dim_juridical_situation;

CREATE OR REPLACE VIEW powerbi."Enterprise" AS
SELECT enterprise_key AS "Enterprise Key", enterprise_number AS "Enterprise Number",
       name AS "Name", enterprise_type_nl AS "Enterprise Type NL",
       enterprise_type_fr AS "Enterprise Type FR", status_code AS "Status"
FROM dwh.dim_enterprise;

CREATE OR REPLACE VIEW powerbi."Enterprise Facts" AS
SELECT enterprise_key AS "Enterprise Key", start_date_key AS "Date Key",
       geography_key AS "Geography Key", main_activity_key AS "Activity Key",
       juridical_form_key AS "Juridical Form Key",
       juridical_situation_key AS "Juridical Situation Key",
       enterprise_count AS "Enterprise Count", establishment_count AS "Establishment Count",
       has_email AS "Has Email", has_phone AS "Has Phone", has_website AS "Has Website",
       age_years AS "Age Years"
FROM dwh.fact_enterprise;

CREATE OR REPLACE VIEW powerbi."Establishment Facts" AS
SELECT establishment_number AS "Establishment Number", enterprise_key AS "Enterprise Key",
       start_date_key AS "Date Key", geography_key AS "Geography Key",
       main_activity_key AS "Activity Key", establishment_count AS "Establishment Count"
FROM dwh.fact_establishment;

-- The extract the report shows, for a "data as of" card.
CREATE OR REPLACE VIEW powerbi."Refresh" AS
SELECT source_extract_number AS "Extract Number", snapshot_date AS "Snapshot Date",
       finished_at AS "Refreshed At"
FROM dwh.etl_batch
ORDER BY batch_id DESC
LIMIT 1;
"""

# Pipeline monitoring: is the data complete, consistent and current? Times are local
# (Europe/Brussels) timestamps: Power BI shows them as they are.
MONITORING_VIEWS = """
-- One row per archive load (kbo.load_history), failed ones included.
CREATE OR REPLACE VIEW powerbi."Load Runs" AS
SELECT run_id::text AS "Run Id", archive AS "Archive", archive_type AS "Archive Type",
       initcap(status) AS "Status", extract_number AS "Extract Number",
       extract_before AS "Extract Before", extract_after AS "Extract After",
       rows_inserted AS "Rows Inserted", rows_deleted AS "Rows Deleted",
       started_at AT TIME ZONE 'Europe/Brussels' AS "Started At",
       finished_at AT TIME ZONE 'Europe/Brussels' AS "Finished At",
       round(extract(epoch FROM finished_at - started_at)::numeric, 1)::float8 AS "Duration Seconds",
       row_number() OVER (ORDER BY started_at, finished_at) AS "Run Order",
       error AS "Error",
       -- unique chart label, e.g. "#8 546 Update (16/11 17:06)"; sorted by "Run Order"
       '#' || row_number() OVER (ORDER BY started_at, finished_at) || ' ' || extract_number
           || ' ' || archive_type || ' ('
           || to_char(started_at AT TIME ZONE 'Europe/Brussels', 'DD/MM HH24:MI') || ')' AS "Run"
FROM kbo.load_history;

-- One row per warehouse refresh (dwh.etl_batch), with the row counts it produced.
CREATE OR REPLACE VIEW powerbi."Warehouse Refreshes" AS
SELECT batch_id AS "Batch Id", source_extract_number AS "Source Extract Number",
       snapshot_date AS "Snapshot Date",
       started_at AT TIME ZONE 'Europe/Brussels' AS "Started At",
       finished_at AT TIME ZONE 'Europe/Brussels' AS "Finished At",
       round(extract(epoch FROM finished_at - started_at)::numeric, 1)::float8 AS "Duration Seconds",
       (row_counts->>'fact_enterprise')::bigint AS "Enterprise Fact Rows",
       (row_counts->>'fact_establishment')::bigint AS "Establishment Fact Rows",
       (row_counts->>'dim_enterprise')::bigint AS "Enterprise Dimension Rows"
FROM dwh.etl_batch;

-- Exact row count of every table, by layer.
CREATE OR REPLACE VIEW powerbi."Table Row Counts" AS
SELECT 'kbo' AS "Layer", t AS "Table", n AS "Rows"
FROM (VALUES
    ('enterprise', (SELECT count(*) FROM kbo.enterprise)),
    ('establishment', (SELECT count(*) FROM kbo.establishment)),
    ('denomination', (SELECT count(*) FROM kbo.denomination)),
    ('address', (SELECT count(*) FROM kbo.address)),
    ('contact', (SELECT count(*) FROM kbo.contact)),
    ('activity', (SELECT count(*) FROM kbo.activity)),
    ('branch', (SELECT count(*) FROM kbo.branch)),
    ('code', (SELECT count(*) FROM kbo.code))) AS source (t, n)
UNION ALL
SELECT 'dwh', t, n
FROM (VALUES
    ('fact_enterprise', (SELECT count(*) FROM dwh.fact_enterprise)),
    ('fact_establishment', (SELECT count(*) FROM dwh.fact_establishment)),
    ('dim_enterprise', (SELECT count(*) FROM dwh.dim_enterprise)),
    ('dim_date', (SELECT count(*) FROM dwh.dim_date)),
    ('dim_geography', (SELECT count(*) FROM dwh.dim_geography)),
    ('dim_activity', (SELECT count(*) FROM dwh.dim_activity)),
    ('dim_juridical_form', (SELECT count(*) FROM dwh.dim_juridical_form)),
    ('dim_juridical_situation', (SELECT count(*) FROM dwh.dim_juridical_situation))) AS warehouse (t, n);

-- Pipeline checks, evaluated when the view is read. "Failing Rows" is 0 when a check
-- passes; Severity says whether a failure is an error or only a warning.
CREATE OR REPLACE VIEW powerbi."Quality Checks" AS
WITH current_extract AS (
    SELECT value::int AS n FROM kbo.meta WHERE variable = 'ExtractNumber'
), latest_full AS (
    SELECT max(extract_number) AS n FROM kbo.load_history
    WHERE archive_type = 'Full' AND status = 'success'
), checks ("Order", "Category", "Check", "Severity", "Failing Rows") AS (
    SELECT 1, 'Integrity', 'Establishments without an enterprise', 'Error',
           (SELECT count(*) FROM kbo.establishment e WHERE NOT EXISTS (
                SELECT 1 FROM kbo.enterprise p WHERE p.enterprise_number = e.enterprise_number))
    UNION ALL
    SELECT 2, 'Integrity', 'Branches without an enterprise', 'Error',
           (SELECT count(*) FROM kbo.branch b WHERE NOT EXISTS (
                SELECT 1 FROM kbo.enterprise p WHERE p.enterprise_number = b.enterprise_number))
    UNION ALL
    SELECT 3, 'Reconciliation', 'Enterprises: source vs warehouse (difference)', 'Error',
           abs((SELECT count(*) FROM kbo.enterprise) - (SELECT count(*) FROM dwh.fact_enterprise))
    UNION ALL
    SELECT 4, 'Reconciliation', 'Establishments: source vs warehouse (difference)', 'Error',
           abs((SELECT count(*) FROM kbo.establishment) - (SELECT count(*) FROM dwh.fact_establishment))
    UNION ALL
    SELECT 5, 'Completeness', 'Legal persons without a registered office', 'Error',
           (SELECT count(*) FROM dwh.fact_enterprise f JOIN dwh.dim_enterprise e USING (enterprise_key)
            WHERE e.enterprise_type_code = '2' AND f.geography_key = 0)
    UNION ALL
    SELECT 6, 'Sequence', 'Updates missing since the last Full load', 'Error',
           (SELECT greatest(c.n - f.n, 0) - (
                SELECT count(DISTINCT extract_number) FROM kbo.load_history
                WHERE archive_type = 'Update' AND status = 'success'
                  AND extract_number > f.n AND extract_number <= c.n)
            FROM current_extract c, latest_full f)
    UNION ALL
    SELECT 7, 'Freshness', 'Warehouse not built from the current extract', 'Error',
           (SELECT (coalesce((SELECT source_extract_number FROM dwh.etl_batch
                              ORDER BY batch_id DESC LIMIT 1), -1) <> c.n)::int
            FROM current_extract c)
    UNION ALL
    SELECT 8, 'Freshness', 'Days since the snapshot date (more than 3)', 'Warning',
           (SELECT greatest(current_date - to_date(value, 'DD-MM-YYYY') - 3, 0)
            FROM kbo.meta WHERE variable = 'SnapshotDate')
    UNION ALL
    SELECT 9, 'Runs', 'Failed loads in the last 7 days', 'Warning',
           (SELECT count(*) FROM kbo.load_history
            WHERE status = 'failed' AND started_at > now() - interval '7 days')
)
SELECT "Order", "Category", "Check", "Severity", "Failing Rows"::bigint AS "Failing Rows",
       CASE WHEN "Failing Rows" = 0 THEN 'Pass'
            WHEN "Severity" = 'Warning' THEN 'Warning' ELSE 'Fail' END AS "Status",
       ("Failing Rows" = 0)::int AS "Passed"
FROM checks;
"""

# Every view above: the tests check the schema holds exactly these.
VIEW_NAMES = [
    "Date", "Geography", "Activity", "Juridical Form", "Juridical Situation", "Enterprise",
    "Enterprise Facts", "Establishment Facts", "Refresh",
    "Load Runs", "Warehouse Refreshes", "Table Row Counts", "Quality Checks",
]

DEFAULT_READER = "powerbi_reader"


def create_views(cur: psycopg.Cursor) -> None:
    """(Re)creates the views. CREATE OR REPLACE keeps the grants already given on them."""
    cur.execute(BUSINESS_VIEWS)
    cur.execute(load.HISTORY_DDL)  # "Load Runs" reads it; IF NOT EXISTS, so a no-op once loaded
    cur.execute(MONITORING_VIEWS)


def grant_reader(conn: psycopg.Connection, role: str, password: str) -> None:
    """Creates (or updates) a login that can read the powerbi views and nothing else."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        verb = "ALTER" if cur.fetchone() else "CREATE"
        # Role names and passwords cannot be bind parameters: quote them instead.
        cur.execute(
            sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.SQL(verb), sql.Identifier(role), sql.Literal(password)
            )
        )
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(conn.info.dbname), sql.Identifier(role)
            )
        )
        cur.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {schema} TO {role};"
                    "GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}").format(
                schema=sql.Identifier(SCHEMA), role=sql.Identifier(role)
            )
        )
    log.info("Read-only role %s can query the %s views", role, SCHEMA)


def grant_reader_from_env(conn: psycopg.Connection) -> None:
    """Applies POWERBI_USER / POWERBI_PASSWORD from the environment, when a password is set."""
    password = os.getenv("POWERBI_PASSWORD")
    if not password:
        log.info("POWERBI_PASSWORD is not set: no read-only Power BI login created")
        return
    grant_reader(conn, os.getenv("POWERBI_USER") or DEFAULT_READER, password)
