import csv
import io
import os
import re
import time
import traceback
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from kbo.extract import (
    ArchiveType,
    _validate_archive_type,
    find_local_archives,
    parse_archive_filename,
)
from kbo.runlog import logger as log, run_id_var

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "kbo"),
    "user": os.getenv("POSTGRES_USER", "root"),
    "password": os.getenv("POSTGRES_PASSWORD", "root"),
    # Per-address timeout: "localhost" can resolve to ::1 first, and if that address
    # never answers (seen with Docker on Windows) libpq would otherwise wait forever
    # instead of falling back to 127.0.0.1.
    "connect_timeout": int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "5")),
}

# (csv_prefix, target_table)
KBO_TABLES: list[tuple[str, str]] = [
    ("enterprise", "kbo.enterprise"),
    ("establishment", "kbo.establishment"),
    ("denomination", "kbo.denomination"),
    ("address", "kbo.address"),
    ("contact", "kbo.contact"),
    ("activity", "kbo.activity"),
    ("branch", "kbo.branch"),
    ("code", "kbo.code"),
]

DDL_STATEMENTS = """
CREATE SCHEMA IF NOT EXISTS kbo;

-- Column names must equal the snake_cased CSV headers: COPY maps columns by name.
CREATE TABLE IF NOT EXISTS kbo.enterprise (
    enterprise_number TEXT PRIMARY KEY,
    status TEXT,
    juridical_situation TEXT,
    type_of_enterprise TEXT,
    juridical_form TEXT,
    juridical_form_cac TEXT,
    start_date DATE
);

CREATE TABLE IF NOT EXISTS kbo.establishment (
    establishment_number TEXT PRIMARY KEY,
    start_date DATE,
    enterprise_number TEXT
);

CREATE TABLE IF NOT EXISTS kbo.denomination (
    entity_number TEXT,
    language TEXT,
    type_of_denomination TEXT,
    denomination TEXT
);

CREATE TABLE IF NOT EXISTS kbo.address (
    entity_number TEXT,
    type_of_address TEXT,
    country_nl TEXT,
    country_fr TEXT,
    zipcode TEXT,
    municipality_nl TEXT,
    municipality_fr TEXT,
    street_nl TEXT,
    street_fr TEXT,
    house_number TEXT,
    box TEXT,
    extra_address_info TEXT,
    date_striking_off DATE
);

CREATE TABLE IF NOT EXISTS kbo.contact (
    entity_number TEXT,
    entity_contact TEXT,
    contact_type TEXT,
    value TEXT
);

CREATE TABLE IF NOT EXISTS kbo.activity (
    entity_number TEXT,
    activity_group TEXT,
    nace_version TEXT,
    nace_code TEXT,
    classification TEXT
);

CREATE TABLE IF NOT EXISTS kbo.branch (
    id TEXT PRIMARY KEY,
    start_date DATE,
    enterprise_number TEXT
);

CREATE TABLE IF NOT EXISTS kbo.meta (
    variable TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS kbo.code (
    category TEXT,
    code TEXT,
    language TEXT,
    description TEXT,
    PRIMARY KEY (category, code, language)
);
"""

INDEX_STATEMENTS = """
CREATE INDEX idx_establishment_ent ON kbo.establishment (enterprise_number);
CREATE INDEX idx_denomination_ent ON kbo.denomination (entity_number);
CREATE INDEX idx_address_ent ON kbo.address (entity_number);
CREATE INDEX idx_address_zip ON kbo.address (zipcode);
CREATE INDEX idx_contact_ent ON kbo.contact (entity_number);
CREATE INDEX idx_activity_ent ON kbo.activity (entity_number);
CREATE INDEX idx_activity_nace ON kbo.activity (nace_version, nace_code);
CREATE INDEX idx_branch_ent ON kbo.branch (enterprise_number);
"""

INDEX_NAMES = re.findall(r"CREATE INDEX (\w+)", INDEX_STATEMENTS)


def _to_snake_case(col_name: str) -> str:
    """Converts KBO PascalCase CSV headers (e.g. JuridicalFormCAC, CountryNL) to snake_case."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", col_name.strip()).lower()


def _find_member(zf: zipfile.ZipFile, filename: str) -> str | None:
    for name in zf.namelist():
        if name == filename or name.endswith(f"/{filename}"):
            return name
    return None


_IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]*")


def _read_columns(zf: zipfile.ZipFile, member_name: str) -> list[str]:
    """Returns the CSV's header as snake_case column names ([] if the file is empty)."""
    with zf.open(member_name) as raw_stream:
        text_stream = io.TextIOWrapper(raw_stream, encoding="utf-8-sig")
        raw_header = next(csv.reader(text_stream), None) or []

    columns = [_to_snake_case(col) for col in raw_header]
    for col in columns:
        # The names are interpolated into SQL, so accept plain identifiers only.
        if not _IDENTIFIER_RE.fullmatch(col):
            raise ValueError(f"Unexpected column name {col!r} in {member_name}.")
    return columns


def _copy_csv(
    cur: psycopg.Cursor,
    zf: zipfile.ZipFile,
    member_name: str,
    target_table: str,
    columns: list[str] | None = None,
) -> int:
    """Streams a CSV inside the ZIP archive directly into Postgres via COPY.

    Columns are matched by header name, not position, so a reordered CSV still loads
    correctly and an unknown column fails loudly instead of landing in the wrong field.
    """
    if columns is None:
        columns = _read_columns(zf, member_name)
    if not columns:
        return 0

    copy_sql = (
        f"COPY {target_table} ({', '.join(columns)}) "
        "FROM STDIN WITH (FORMAT csv, HEADER true, DELIMITER ',')"
    )
    with zf.open(member_name) as csv_stream:
        with cur.copy(copy_sql) as copy:
            while chunk := csv_stream.read(1024 * 1024):
                copy.write(chunk)
    return cur.rowcount


def _apply_deletes(
    cur: psycopg.Cursor, zf: zipfile.ZipFile, member_name: str, target_table: str
) -> int:
    """Stages *_delete.csv into a TEMP table and deletes matching rows from target.

    Temp table with TEXT columns, COPY the delete CSV in, then DELETE via a
    plain-equality join on every column.
    """
    header = _read_columns(zf, member_name)
    if not header:
        return 0

    col_defs = ", ".join(f"{col} TEXT" for col in header)
    join_cond = " AND ".join(f"t.{col} = s.{col}" for col in header)

    # pg_temp-qualified throughout, so a permanent table that happens to be called
    # _stg_delete somewhere on the search_path can never be the one touched.
    cur.execute("DROP TABLE IF EXISTS pg_temp._stg_delete;")
    cur.execute(f"CREATE TEMP TABLE _stg_delete ({col_defs}) ON COMMIT DROP;")

    _copy_csv(cur, zf, member_name, "pg_temp._stg_delete", columns=header)

    cur.execute(
        f"DELETE FROM {target_table} AS t USING pg_temp._stg_delete AS s WHERE {join_cond};"
    )
    return cur.rowcount


@dataclass
class LoadStats:
    """What one archive load did; also what is written to kbo.load_history."""

    extract_number: int | None = None
    extract_before: int | None = None
    extract_after: int | None = None
    rows_inserted: int = 0
    rows_deleted: int = 0


class UpdateSequenceError(RuntimeError):
    """Raised when an Update archive is not the next one the database expects."""


class ArchiveMismatchError(ValueError):
    """Raised when an archive's meta.csv contradicts its file name or the requested type."""


def _read_archive_meta(zf: zipfile.ZipFile) -> dict[str, str]:
    """Returns meta.csv from the archive as a {Variable: Value} dict."""
    member = _find_member(zf, "meta.csv")
    if not member:
        raise KeyError("Expected 'meta.csv' in archive.")
    with zf.open(member) as raw_stream:
        reader = csv.reader(io.TextIOWrapper(raw_stream, encoding="utf-8"))
        next(reader, None)  # header
        return {row[0]: row[1] for row in reader if len(row) >= 2}


def check_archive_identity(filename: str, archive_type: ArchiveType, meta: dict[str, str]) -> int:
    """Checks that meta.csv describes the archive we think we are loading.

    The file name and the requested type say what the archive should be; meta.csv says
    what it is. Returns the extract number once they agree.
    """
    meta_type = meta.get("ExtractType", "").strip().lower()
    if meta_type != archive_type.lower():
        raise ArchiveMismatchError(
            f"{filename} is being loaded as {archive_type}, "
            f"but its meta.csv says ExtractType={meta_type!r}."
        )

    try:
        number = int(meta.get("ExtractNumber", ""))
    except ValueError:
        raise ArchiveMismatchError(
            f"{filename} has no usable ExtractNumber in meta.csv "
            f"(got {meta.get('ExtractNumber')!r})."
        ) from None

    named = parse_archive_filename(filename)
    if named and named.archive_type != archive_type:
        raise ArchiveMismatchError(
            f"{filename} is named as a {named.archive_type} archive but is being loaded as {archive_type}."
        )
    if named and named.number != number:
        raise ArchiveMismatchError(
            f"{filename} is named as extract {named.number}, but its meta.csv says extract {number}."
        )
    return number


def check_extract_order(current: int, incoming: int) -> None:
    """Raises unless `incoming` is exactly the extract after `current`.

    Updates are deltas, so skipping one, applying one twice, or applying them out
    of order silently corrupts the data.
    """
    if incoming == current + 1:
        return
    if incoming <= current:
        raise UpdateSequenceError(
            f"Update {incoming} is already applied (database is at extract {current})."
        )
    raise UpdateSequenceError(
        f"Update {incoming} is out of sequence: database is at extract {current}, "
        f"so extract {current + 1} must be applied first."
    )


def current_extract_number(conn: psycopg.Connection) -> int | None:
    """The extract the database is at, or None before the first Full load."""
    if not conn.execute("SELECT to_regclass('kbo.meta') IS NOT NULL").fetchone()[0]:
        return None
    row = conn.execute("SELECT value FROM kbo.meta WHERE variable = 'ExtractNumber'").fetchone()
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _lock_and_check_sequence(cur: psycopg.Cursor, incoming: int) -> None:
    """Inside the load transaction: the final guard against a wrong or concurrent update."""
    cur.execute("SELECT to_regclass('kbo.meta') IS NOT NULL;")
    if not cur.fetchone()[0]:
        raise UpdateSequenceError(
            "kbo.meta does not exist; run a Full load before applying updates."
        )

    # FOR UPDATE serialises concurrent runs: the second waits, then sees the new number.
    cur.execute("SELECT value FROM kbo.meta WHERE variable = 'ExtractNumber' FOR UPDATE;")
    row = cur.fetchone()
    try:
        current = int(row[0])
    except (TypeError, ValueError):
        raise UpdateSequenceError(
            f"kbo.meta has no usable ExtractNumber (got {row[0] if row else None!r})."
        ) from None

    check_extract_order(current, incoming)


def _load_full(cur: psycopg.Cursor, zf: zipfile.ZipFile, stats: LoadStats) -> None:
    log.info("Preparing schema and emptying tables...")
    cur.execute(DDL_STATEMENTS)
    tables = [*KBO_TABLES, ("meta", "kbo.meta")]
    # TRUNCATE instead of DROP ... CASCADE: views and other objects built on these
    # tables survive a Full load. Like the rest of the load it is rolled back on failure.
    cur.execute(f"TRUNCATE {', '.join(table for _, table in tables)};")
    # Secondary indexes are rebuilt after the bulk load, which is much faster.
    for index in INDEX_NAMES:
        cur.execute(f"DROP INDEX IF EXISTS kbo.{index};")

    for prefix, table_name in tables:
        t0 = time.perf_counter()
        member = _find_member(zf, f"{prefix}.csv")
        if not member:
            raise KeyError(f"Expected '{prefix}.csv' in Full archive.")

        inserted = _copy_csv(cur, zf, member, table_name)
        stats.rows_inserted += inserted
        log.info("Loaded  %-18s (%s rows) in %.2fs", table_name, f"{inserted:,}", time.perf_counter() - t0)

    log.info("Building indexes and updating statistics...")
    t0 = time.perf_counter()
    cur.execute(INDEX_STATEMENTS)
    for _, table_name in tables:
        cur.execute(f"ANALYZE {table_name};")
    log.info("Indexes and stats ready in %.2fs", time.perf_counter() - t0)


def _load_update(cur: psycopg.Cursor, zf: zipfile.ZipFile, stats: LoadStats) -> None:
    _lock_and_check_sequence(cur, stats.extract_number)
    log.info("Applying update extract %s...", stats.extract_number)

    for prefix, table_name in KBO_TABLES:
        t0 = time.perf_counter()

        del_member = _find_member(zf, f"{prefix}_delete.csv")
        ins_member = _find_member(zf, f"{prefix}_insert.csv")
        full_member = _find_member(zf, f"{prefix}.csv")

        if del_member or ins_member:
            deleted = _apply_deletes(cur, zf, del_member, table_name) if del_member else 0
            inserted = _copy_csv(cur, zf, ins_member, table_name) if ins_member else 0
            stats.rows_deleted += deleted
            stats.rows_inserted += inserted
            log.info(
                "Updated %-18s (-%s / +%s rows) in %.2fs",
                table_name, f"{deleted:,}", f"{inserted:,}", time.perf_counter() - t0,
            )
        elif full_member:
            # Update archives ship some tables (kbo.code) as a complete snapshot.
            # DELETE rather than TRUNCATE: it is MVCC-safe for concurrent readers.
            # A snapshot swap is not a delta, so it is left out of the row totals.
            cur.execute(f"DELETE FROM {table_name};")
            inserted = _copy_csv(cur, zf, full_member, table_name)
            log.info(
                "Replaced %-17s (%s rows) in %.2fs",
                table_name, f"{inserted:,}", time.perf_counter() - t0,
            )
        else:
            log.info("Skipped %-18s (no CSV for it in this archive)", table_name)

    cur.execute("DELETE FROM kbo.meta;")
    _copy_csv(cur, zf, _find_member(zf, "meta.csv"), "kbo.meta")


HISTORY_DDL = """
CREATE SCHEMA IF NOT EXISTS kbo;
CREATE TABLE IF NOT EXISTS kbo.load_history (
    run_id UUID PRIMARY KEY,
    archive TEXT NOT NULL,
    archive_type TEXT NOT NULL,
    status TEXT NOT NULL,           -- 'success' or 'failed'
    extract_number INTEGER,         -- the archive's extract
    extract_before INTEGER,         -- database extract before / after the run
    extract_after INTEGER,
    rows_inserted BIGINT,
    rows_deleted BIGINT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    error TEXT,
    traceback TEXT
);
"""


def _record_run(conn, run_id, archive, archive_type, status, stats, started_at, error, tb) -> None:
    """Writes one row to kbo.load_history. Never raises: history must not mask the load's outcome."""
    try:
        conn.execute(HISTORY_DDL)
        conn.execute(
            """
            INSERT INTO kbo.load_history (
                run_id, archive, archive_type, status, extract_number, extract_before,
                extract_after, rows_inserted, rows_deleted, started_at, finished_at,
                error, traceback
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
            """,
            (
                run_id, archive, archive_type, status, stats.extract_number,
                stats.extract_before, stats.extract_after, stats.rows_inserted,
                stats.rows_deleted, started_at, error, tb,
            ),
        )
    except psycopg.Error as exc:
        log.warning("Could not record this run in kbo.load_history: %s", exc)


# Rows pointing at an enterprise that does not exist. Checked after each load instead of
# enforced with foreign keys, which the registry's own data does not always satisfy.
ORPHAN_CHECKS = {
    "establishments without an enterprise": """
        SELECT count(*) FROM kbo.establishment e WHERE NOT EXISTS (
            SELECT 1 FROM kbo.enterprise p WHERE p.enterprise_number = e.enterprise_number)""",
    "branches without an enterprise": """
        SELECT count(*) FROM kbo.branch b WHERE NOT EXISTS (
            SELECT 1 FROM kbo.enterprise p WHERE p.enterprise_number = b.enterprise_number)""",
}


def find_orphans(conn: psycopg.Connection) -> dict[str, int]:
    """Counts rows whose parent enterprise is missing: {description: count}."""
    return {name: conn.execute(sql).fetchone()[0] for name, sql in ORPHAN_CHECKS.items()}


def _log_integrity(conn: psycopg.Connection) -> None:
    try:
        orphans = {name: count for name, count in find_orphans(conn).items() if count}
    except psycopg.Error as exc:
        log.warning("Integrity check could not run: %s", exc)
        return
    if not orphans:
        log.info("Integrity check passed: no orphaned establishments or branches.")
    for name, count in orphans.items():
        log.warning("Integrity check: %s %s.", f"{count:,}", name)


def _connect() -> psycopg.Connection:
    # autocommit, so each `conn.transaction()` block is one real transaction and the
    # connection stays usable (for the history row) after a failed load is rolled back.
    conn = psycopg.connect(**DB_CONFIG, autocommit=True)
    # KBO exports use DD-MM-YYYY dates; DMY lets Postgres cast natively during COPY
    conn.execute("SET datestyle = 'DMY';")
    return conn


def load_archive(
    zip_path: Path | str,
    archive_type: ArchiveType = "Update",
    conn: psycopg.Connection | None = None,
) -> LoadStats:
    """
    Loads one KBO archive file into PostgreSQL, in a single transaction.
    - 'Full': Empties and refills all kbo.* tables via COPY, and rebuilds indexes.
    - 'Update': Applies *_delete.csv and *_insert.csv deltas and refreshes kbo.meta.

    Every attempt, successful or not, is logged under its own run ID and recorded in
    kbo.load_history. `conn` must be an autocommit connection (see `_connect`).
    """
    _validate_archive_type(archive_type)

    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP archive not found at: {zip_path}")

    if conn is None:
        with _connect() as own_conn:
            return load_archive(zip_path, archive_type, own_conn)

    run_id = uuid.uuid4()
    token = run_id_var.set(run_id.hex[:8])
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    stats = LoadStats()
    status, error, tb = "success", None, None

    try:
        log.info("Starting %s load of %s", archive_type, zip_path.name)
        with zipfile.ZipFile(zip_path, "r") as zf:
            stats.extract_number = check_archive_identity(
                zip_path.name, archive_type, _read_archive_meta(zf)
            )
            stats.extract_before = current_extract_number(conn)

            with conn.transaction(), conn.cursor() as cur:
                if archive_type == "Full":
                    _load_full(cur, zf, stats)
                else:
                    _load_update(cur, zf, stats)

        stats.extract_after = current_extract_number(conn)
        log.info(
            "Finished %s load of %s in %.2fs: extract %s -> %s, +%s / -%s rows",
            archive_type, zip_path.name, time.perf_counter() - t0,
            stats.extract_before, stats.extract_after,
            f"{stats.rows_inserted:,}", f"{stats.rows_deleted:,}",
        )
        _log_integrity(conn)
    except Exception as exc:
        status, error, tb = "failed", f"{type(exc).__name__}: {exc}", traceback.format_exc()
        log.exception(
            "Failed %s load of %s after %.2fs; the database was left at extract %s",
            archive_type, zip_path.name, time.perf_counter() - t0, stats.extract_before,
        )
        raise
    finally:
        _record_run(conn, run_id, zip_path.name, archive_type, status, stats, started_at, error, tb)
        run_id_var.reset(token)

    return stats


def load_kbo_to_postgres(
    datevar: date,
    archive_type: ArchiveType = "Update",
    data_dir: Path | str = "data",
) -> None:
    """
    Loads the local KBO archive(s) of `datevar` from `data_dir` into PostgreSQL.

    The portal can publish more than one extract on the same date: Updates are then
    applied in extract-number order, and for a Full only the newest one is loaded.
    Updates the database already has are skipped, so a run that failed part-way
    (extract 541 applied, 542 not) can simply be repeated.
    """
    archives = find_local_archives(data_dir, datevar, archive_type)
    if not archives:
        raise FileNotFoundError(
            f"No {archive_type} archive for {datevar:%Y-%m-%d} found in: {data_dir}"
        )
    if archive_type == "Full":
        archives = archives[-1:]

    with _connect() as conn:
        if archive_type == "Update":
            current = current_extract_number(conn)
            if current is not None:
                for archive in archives:
                    if archive.number <= current:
                        log.info(
                            "Skipping %s: extract %d is already applied (database is at extract %d).",
                            archive.filename, archive.number, current,
                        )
                # Anything left is still checked against the sequence inside its own
                # transaction; this filter only makes repeating a run harmless.
                archives = [a for a in archives if a.number > current]

        for archive in archives:
            load_archive(Path(data_dir) / archive.filename, archive_type=archive_type, conn=conn)
