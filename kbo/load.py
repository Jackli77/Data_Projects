import csv
import io
import os
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Literal
import re

import psycopg
from dotenv import load_dotenv

from kbo.extract import ArchiveType, _validate_archive_type, find_local_archives

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

DROP TABLE IF EXISTS
    kbo.enterprise,
    kbo.establishment,
    kbo.denomination,
    kbo.address,
    kbo.contact,
    kbo.activity,
    kbo.branch,
    kbo.meta,
    kbo.code
CASCADE;

-- Column names must equal the snake_cased CSV headers: COPY maps columns by name.
CREATE TABLE kbo.enterprise (
    enterprise_number TEXT PRIMARY KEY,
    status TEXT,
    juridical_situation TEXT,
    type_of_enterprise TEXT,
    juridical_form TEXT,
    juridical_form_cac TEXT,
    start_date DATE
);

CREATE TABLE kbo.establishment (
    establishment_number TEXT PRIMARY KEY,
    start_date DATE,
    enterprise_number TEXT
);

CREATE TABLE kbo.denomination (
    entity_number TEXT,
    language TEXT,
    type_of_denomination TEXT,
    denomination TEXT
);

CREATE TABLE kbo.address (
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

CREATE TABLE kbo.contact (
    entity_number TEXT,
    entity_contact TEXT,
    contact_type TEXT,
    value TEXT
);

CREATE TABLE kbo.activity (
    entity_number TEXT,
    activity_group TEXT,
    nace_version TEXT,
    nace_code TEXT,
    classification TEXT
);

CREATE TABLE kbo.branch (
    id TEXT PRIMARY KEY,
    start_date DATE,
    enterprise_number TEXT
);

CREATE TABLE kbo.meta (
    variable TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE kbo.code (
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

    Mirrors the fast path in load_update.py: temp table with TEXT columns, COPY the
    delete CSV in, then DELETE via a plain-equality join on every column.
    """
    header = _read_columns(zf, member_name)
    if not header:
        return 0

    col_defs = ", ".join(f"{col} TEXT" for col in header)
    join_cond = " AND ".join(f"t.{col} = s.{col}" for col in header)

    cur.execute("DROP TABLE IF EXISTS _stg_delete;")
    cur.execute(f"CREATE TEMP TABLE _stg_delete ({col_defs}) ON COMMIT DROP;")

    _copy_csv(cur, zf, member_name, "_stg_delete", columns=header)

    cur.execute(
        f"DELETE FROM {target_table} AS t USING _stg_delete AS s WHERE {join_cond};"
    )
    deleted = cur.rowcount
    return deleted

def _load_full(cur: psycopg.Cursor, zf: zipfile.ZipFile) -> None:
    print("Creating schema and tables...")
    cur.execute(DDL_STATEMENTS)

    for prefix, table_name in [*KBO_TABLES, ("meta", "kbo.meta")]:
        t0 = time.perf_counter()
        member = _find_member(zf, f"{prefix}.csv")
        if not member:
            raise KeyError(f"Expected '{prefix}.csv' in Full archive.")

        inserted = _copy_csv(cur, zf, member, table_name)
        print(f"Loaded  {table_name:<18} ({inserted:,} rows) in {time.perf_counter() - t0:.2f}s")

    print("Building indexes and updating statistics...")
    t0 = time.perf_counter()
    cur.execute(INDEX_STATEMENTS)
    for _, table_name in [*KBO_TABLES, ("meta", "kbo.meta")]:
        cur.execute(f"ANALYZE {table_name};")
    print(f"Indexes and stats ready in {time.perf_counter() - t0:.2f}s")


class UpdateSequenceError(RuntimeError):
    """Raised when an Update archive is not the next one the database expects."""


def _read_archive_meta(zf: zipfile.ZipFile) -> dict[str, str]:
    """Returns meta.csv from the archive as a {Variable: Value} dict."""
    member = _find_member(zf, "meta.csv")
    if not member:
        raise KeyError("Expected 'meta.csv' in archive.")
    with zf.open(member) as raw_stream:
        reader = csv.reader(io.TextIOWrapper(raw_stream, encoding="utf-8"))
        next(reader, None)  # header
        return {row[0]: row[1] for row in reader if len(row) >= 2}


def _parse_extract_number(value: str | None, source: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise UpdateSequenceError(
            f"{source} has no usable ExtractNumber (got {value!r})."
        ) from None


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


def _check_update_sequence(cur: psycopg.Cursor, zf: zipfile.ZipFile) -> int:
    """Checks the archive against kbo.meta and returns the archive's ExtractNumber."""
    archive_meta = _read_archive_meta(zf)
    extract_type = archive_meta.get("ExtractType", "").lower()
    if extract_type != "update":
        raise UpdateSequenceError(
            f"Archive is not an Update extract (ExtractType={extract_type!r})."
        )
    incoming = _parse_extract_number(archive_meta.get("ExtractNumber"), "Archive meta.csv")

    cur.execute("SELECT to_regclass('kbo.meta') IS NOT NULL;")
    if not cur.fetchone()[0]:
        raise UpdateSequenceError(
            "kbo.meta does not exist; run a Full load before applying updates."
        )

    # FOR UPDATE serialises concurrent runs: the second waits, then sees the new number.
    cur.execute("SELECT value FROM kbo.meta WHERE variable = 'ExtractNumber' FOR UPDATE;")
    row = cur.fetchone()
    current = _parse_extract_number(row[0] if row else None, "kbo.meta")

    check_extract_order(current, incoming)
    return incoming


def _load_update(cur: psycopg.Cursor, zf: zipfile.ZipFile) -> None:
    extract_number = _check_update_sequence(cur, zf)
    print(f"Applying update extract {extract_number}...")

    for prefix, table_name in KBO_TABLES:
        t0 = time.perf_counter()

        del_member = _find_member(zf, f"{prefix}_delete.csv")
        ins_member = _find_member(zf, f"{prefix}_insert.csv")
        full_member = _find_member(zf, f"{prefix}.csv")

        if del_member or ins_member:
            deleted = _apply_deletes(cur, zf, del_member, table_name) if del_member else 0
            inserted = _copy_csv(cur, zf, ins_member, table_name) if ins_member else 0
            print(
                f"Updated {table_name:<18} "
                f"(-{deleted:,} / +{inserted:,} rows) in {time.perf_counter() - t0:.2f}s"
            )
        elif full_member:
            # Update archives ship some tables (kbo.code) as a complete snapshot.
            # DELETE rather than TRUNCATE: it is MVCC-safe for concurrent readers.
            cur.execute(f"DELETE FROM {table_name};")
            inserted = _copy_csv(cur, zf, full_member, table_name)
            print(
                f"Replaced {table_name:<17} "
                f"({inserted:,} rows) in {time.perf_counter() - t0:.2f}s"
            )
        else:
            print(f"Skipped {table_name:<18} (no CSV for it in this archive)")

    cur.execute("DELETE FROM kbo.meta;")
    meta_rows = _copy_csv(cur, zf, _find_member(zf, "meta.csv"), "kbo.meta")
    print(f"Refresh {'kbo.meta':<18} ({meta_rows:,} rows)")


def load_archive(zip_path: Path | str, archive_type: ArchiveType = "Update") -> None:
    """
    Loads one KBO archive file into PostgreSQL, in a single transaction.
    - 'Full': Recreates all kbo.* tables, streams CSVs via COPY, and builds indexes.
    - 'Update': Applies *_delete.csv and *_insert.csv deltas and refreshes kbo.meta.
    """
    _validate_archive_type(archive_type)

    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP archive not found at: {zip_path}")

    total_start = time.perf_counter()

    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            # KBO exports use DD-MM-YYYY dates; DMY lets Postgres cast natively during COPY
            cur.execute("SET datestyle = 'DMY';")

            with zipfile.ZipFile(zip_path, "r") as zf:
                if archive_type == "Full":
                    _load_full(cur, zf)
                else:
                    _load_update(cur, zf)

        conn.commit()

    print(
        f"\nDone! {archive_type} load of {zip_path.name} "
        f"completed in {time.perf_counter() - total_start:.2f}s"
    )


def load_kbo_to_postgres(
    datevar: date,
    archive_type: ArchiveType = "Update",
    data_dir: Path | str = "data",
) -> None:
    """
    Loads the local KBO archive(s) of `datevar` from `data_dir` into PostgreSQL.

    The portal can publish more than one extract on the same date: Updates are then
    applied in extract-number order, and for a Full only the newest one is loaded.
    """
    archives = find_local_archives(data_dir, datevar, archive_type)
    if not archives:
        raise FileNotFoundError(
            f"No {archive_type} archive for {datevar:%Y-%m-%d} found in: {data_dir}"
        )
    if archive_type == "Full":
        archives = archives[-1:]

    for archive in archives:
        load_archive(Path(data_dir) / archive.filename, archive_type=archive_type)
