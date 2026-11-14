import csv
import io
import os
from datetime import date, datetime
from pathlib import Path
import time
import zipfile

import psycopg

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "kbo"),
    "user": os.getenv("POSTGRES_USER", "root"),
    "password": os.getenv("POSTGRES_PASSWORD", "root"),
}

# (base_csv_prefix, target_table)
# KBO updates ship pairs of <prefix>_delete.csv and <prefix>_insert.csv
TABLES_TO_UPDATE = [
    ("enterprise", "kbo.enterprise"),
    ("establishment", "kbo.establishment"),
    ("denomination", "kbo.denomination"),
    ("address", "kbo.address"),
    ("contact", "kbo.contact"),
    ("activity", "kbo.activity"),
    ("branch", "kbo.branch"),
    ("code", "kbo.code"),
]


def get_update_filename(datevar: date) -> str:
    start_date = date(2025, 5, 20)
    file_id = (datevar - start_date).days + 1
    return f"KboOpenData_{file_id:04d}_{datevar:%Y_%m_%d}_Update.zip"


def _find_member(zf: zipfile.ZipFile, filename: str) -> str | None:
    for name in zf.namelist():
        if name == filename or name.endswith(f"/{filename}"):
            return name
    return None


def _apply_deletes(cur: psycopg.Cursor, zf: zipfile.ZipFile, member_name: str, table_name: str) -> int:
    """Stages *_delete.csv into a TEMP table and deletes matching rows from target."""
    with zf.open(member_name) as raw_stream:
        text_stream = io.TextIOWrapper(raw_stream, encoding="utf-8")
        header = next(csv.reader(text_stream), None)

    if not header:
        return 0

    col_defs = ", ".join(f"{col} TEXT" for col in header)
    join_cond = " AND ".join(f"t.{col} = s.{col}" for col in header)

    cur.execute("DROP TABLE IF EXISTS _stg_delete;")
    cur.execute(f"CREATE TEMP TABLE _stg_delete ({col_defs}) ON COMMIT DROP;")

    copy_sql = "COPY _stg_delete FROM STDIN WITH (FORMAT csv, HEADER true, DELIMITER ',')"
    with zf.open(member_name) as csv_stream:
        with cur.copy(copy_sql) as copy:
            while chunk := csv_stream.read(1024 * 1024):
                copy.write(chunk)

    cur.execute(f"DELETE FROM {table_name} AS t USING _stg_delete AS s WHERE {join_cond};")
    return cur.rowcount


def _apply_inserts(cur: psycopg.Cursor, zf: zipfile.ZipFile, member_name: str, table_name: str) -> int:
    """Streams *_insert.csv directly into the target table via COPY."""
    copy_sql = f"COPY {table_name} FROM STDIN WITH (FORMAT csv, HEADER true, DELIMITER ',')"
    with zf.open(member_name) as csv_stream:
        with cur.copy(copy_sql) as copy:
            while chunk := csv_stream.read(1024 * 1024):
                copy.write(chunk)
    return cur.rowcount


def load_kbo_update_to_postgres(datevar: date, data_dir: Path | None = None) -> None:
    base_dir = data_dir or Path(__file__).resolve().parent
    zip_path = base_dir/"downloads" / get_update_filename(datevar)

    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP archive not found at: {zip_path}")

    total_start = time.perf_counter()

    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("SET datestyle = 'DMY';")

            with zipfile.ZipFile(zip_path) as zf:
                for prefix, table_name in TABLES_TO_UPDATE:
                    t0 = time.perf_counter()
                    deleted = 0
                    inserted = 0

                    del_member = _find_member(zf, f"{prefix}_delete.csv")
                    if del_member:
                        deleted = _apply_deletes(cur, zf, del_member, table_name)

                    ins_member = _find_member(zf, f"{prefix}_insert.csv")
                    if ins_member:
                        inserted = _apply_inserts(cur, zf, ins_member, table_name)

                    elapsed = time.perf_counter() - t0
                    print(
                        f"Updated {table_name:<18} "
                        f"(-{deleted:,} / +{inserted:,} rows) in {elapsed:.2f}s"
                    )

                # Refresh meta.csv (shipped as a single replacement file in updates)
                meta_member = _find_member(zf, "meta.csv")
                if meta_member:
                    cur.execute("TRUNCATE TABLE kbo.meta;")
                    meta_rows = _apply_inserts(cur, zf, meta_member, "kbo.meta")
                    print(f"Refreshed {'kbo.meta':<18} ({meta_rows:,} rows)")

        # Commits all deletes and inserts atomically
        conn.commit()

    print(f"\nDone! Update for {datevar:%Y-%m-%d} completed in {time.perf_counter() - total_start:.2f}s")


if __name__ == "__main__":
    raw_date = input("Enter update date (YYYY-MM-DD): ").strip()
    target_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    load_kbo_update_to_postgres(target_date)