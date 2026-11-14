import csv
import io
import os
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Literal
import re

import psycopg
from dotenv import load_dotenv

from extract import ArchiveType, VALID_ARCHIVE_TYPES, _validate_archive_type, archive_filename

load_dotenv(override=True)

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "kbo"),
    "user": os.getenv("POSTGRES_USER", "root"),
    "password": os.getenv("POSTGRES_PASSWORD", "root"),
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

DDL_STATEMENTS: list[str] = [
    "CREATE SCHEMA IF NOT EXISTS kbo;",
    """
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
    """,
    """
    CREATE TABLE kbo.enterprise (
        enterprise_number VARCHAR(12) PRIMARY KEY,
        status VARCHAR(10),
        juridical_situation VARCHAR(10),
        type_of_enterprise VARCHAR(10),
        juridical_form VARCHAR(10),
        juridical_form_cac VARCHAR(10),
        start_date DATE
    );
    """,
    """
    CREATE TABLE kbo.establishment (
        establishment_number VARCHAR(13) PRIMARY KEY,
        start_date DATE,
        enterprise_number VARCHAR(12)
    );
    """,
    """
    CREATE TABLE kbo.denomination (
        entity_number VARCHAR(13),
        language VARCHAR(5),
        type_of_denomination VARCHAR(10),
        denomination TEXT
    );
    """,
    """
    CREATE TABLE kbo.address (
        entity_number VARCHAR(13),
        type_of_address VARCHAR(10),
        country_nl TEXT,
        country_fr TEXT,
        zipcode VARCHAR(20),
        municipality_nl TEXT,
        municipality_fr TEXT,
        street_nl TEXT,
        street_fr TEXT,
        house_number VARCHAR(20),
        box VARCHAR(20),
        extra_address_info TEXT,
        date_striking_off DATE
    );
    """,
    """
    CREATE TABLE kbo.contact (
        entity_number VARCHAR(13),
        entity_contact VARCHAR(10),
        contact_type VARCHAR(20),
        value TEXT
    );
    """,
    """
    CREATE TABLE kbo.activity (
        entity_number VARCHAR(13),
        activity_group VARCHAR(10),
        nace_version VARCHAR(10),
        nace_code VARCHAR(10),
        classification VARCHAR(10)
    );
    """,
    """
    CREATE TABLE kbo.branch (
        id VARCHAR(13) PRIMARY KEY,
        start_date DATE,
        enterprise_number VARCHAR(12)
    );
    """,
    """
    CREATE TABLE kbo.meta (
        variable VARCHAR(50) PRIMARY KEY,
        value TEXT
    );
    """,
    """
    CREATE TABLE kbo.code (
        category VARCHAR(50),
        code VARCHAR(20),
        language VARCHAR(5),
        description TEXT,
        PRIMARY KEY (category, code, language)
    );
    """,
]

INDEX_STATEMENTS: list[str] = [
    "CREATE INDEX idx_establishment_ent ON kbo.establishment (enterprise_number);",
    "CREATE INDEX idx_denomination_ent ON kbo.denomination (entity_number);",
    "CREATE INDEX idx_address_ent ON kbo.address (entity_number);",
    "CREATE INDEX idx_address_zip ON kbo.address (zipcode);",
    "CREATE INDEX idx_contact_ent ON kbo.contact (entity_number);",
    "CREATE INDEX idx_activity_ent ON kbo.activity (entity_number);",
    "CREATE INDEX idx_activity_nace ON kbo.activity (nace_version, nace_code);",
    "CREATE INDEX idx_branch_ent ON kbo.branch (enterprise_number);",
]

def _to_snake_case(col_name: str) -> str:
    """Converts KBO PascalCase CSV headers (e.g. JuridicalFormCAC, CountryNL) to snake_case."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", col_name.strip()).lower()


def _find_member(zf: zipfile.ZipFile, filename: str) -> str | None:
    for name in zf.namelist():
        if name == filename or name.endswith(f"/{filename}"):
            return name
    return None


def _copy_csv(cur: psycopg.Cursor, zf: zipfile.ZipFile, member_name: str, target_table: str) -> int:
    """Streams a CSV inside the ZIP archive directly into Postgres via COPY."""
    copy_sql = f"COPY {target_table} FROM STDIN WITH (FORMAT csv, HEADER true, DELIMITER ',')"
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
    with zf.open(member_name) as raw_stream:
        text_stream = io.TextIOWrapper(raw_stream, encoding="utf-8")
        raw_header = next(csv.reader(text_stream), None)

    if not raw_header:
        return 0

    header = [_to_snake_case(col) for col in raw_header]
    col_defs = ", ".join(f"{col} TEXT" for col in header)
    join_cond = " AND ".join(f"t.{col} = s.{col}" for col in header)

    cur.execute("DROP TABLE IF EXISTS _stg_delete;")
    cur.execute(f"CREATE TEMP TABLE _stg_delete ({col_defs}) ON COMMIT DROP;")

    _copy_csv(cur, zf, member_name, "_stg_delete")

    cur.execute(
        f"DELETE FROM {target_table} AS t USING _stg_delete AS s WHERE {join_cond};"
    )
    deleted = cur.rowcount
    return deleted

def _load_full(cur: psycopg.Cursor, zf: zipfile.ZipFile) -> None:
    print("Creating schema and tables...")
    for stmt in DDL_STATEMENTS:
        cur.execute(stmt)

    for prefix, table_name in [*KBO_TABLES, ("meta", "kbo.meta")]:
        t0 = time.perf_counter()
        member = _find_member(zf, f"{prefix}.csv")
        if not member:
            raise KeyError(f"Expected '{prefix}.csv' in Full archive.")

        inserted = _copy_csv(cur, zf, member, table_name)
        print(f"Loaded  {table_name:<18} ({inserted:,} rows) in {time.perf_counter() - t0:.2f}s")

    print("Building indexes and updating statistics...")
    t0 = time.perf_counter()
    for stmt in INDEX_STATEMENTS:
        cur.execute(stmt)
    for _, table_name in [*KBO_TABLES, ("meta", "kbo.meta")]:
        cur.execute(f"ANALYZE {table_name};")
    print(f"Indexes and stats ready in {time.perf_counter() - t0:.2f}s")


def _load_update(cur: psycopg.Cursor, zf: zipfile.ZipFile) -> None:
    for prefix, table_name in KBO_TABLES:
        t0 = time.perf_counter()
        deleted = 0
        inserted = 0

        del_member = _find_member(zf, f"{prefix}_delete.csv")
        if del_member:
            deleted = _apply_deletes(cur, zf, del_member, table_name)

        ins_member = _find_member(zf, f"{prefix}_insert.csv")
        if ins_member:
            inserted = _copy_csv(cur, zf, ins_member, table_name)

        print(
            f"Updated {table_name:<18} "
            f"(-{deleted:,} / +{inserted:,} rows) in {time.perf_counter() - t0:.2f}s"
        )

    meta_member = _find_member(zf, "meta.csv")
    if meta_member:
        cur.execute("TRUNCATE TABLE kbo.meta;")
        meta_rows = _copy_csv(cur, zf, meta_member, "kbo.meta")
        print(f"Refresh {'kbo.meta':<18} ({meta_rows:,} rows)")


def load_kbo_to_postgres(
    datevar: date,
    archive_type: ArchiveType = "Update",
    data_dir: Path | str = "data",
) -> None:
    """
    Loads a KBO archive ('Full' or 'Update') for `datevar` into PostgreSQL.
    - 'Full': Recreates all kbo.* tables, streams CSVs via COPY, and builds indexes.
    - 'Update': Applies *_delete.csv and *_insert.csv deltas and refreshes kbo.meta.
    """
    _validate_archive_type(archive_type)

    zip_path = Path(data_dir) / archive_filename(datevar, archive_type=archive_type)
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
        f"\nDone! {archive_type} load for {datevar:%Y-%m-%d} "
        f"completed in {time.perf_counter() - total_start:.2f}s"
    )


if __name__ == "__main__":
    raw_type = input("Archive type [Full/Update] (default: Update): ").strip() or "Update"
    if raw_type not in VALID_ARCHIVE_TYPES:
        raise ValueError(f"Invalid archive type {raw_type!r}. Choose 'Full' or 'Update'.")

    raw_date = input("Archive date (YYYY-MM-DD): ").strip()
    target_date = datetime.strptime(raw_date, "%Y-%m-%d").date()

    load_kbo_to_postgres(target_date, archive_type=raw_type, data_dir="downloads")