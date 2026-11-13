import os
from pathlib import Path
import time
import zipfile

import psycopg

ZIP_FILENAME = "KboOpenData_0540_2026_11_10_Full.zip"

# Use "localhost" when running from your terminal,
# or "pgdatabase" when running inside a Docker Compose service.
DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "kbo"),
    "user": os.getenv("POSTGRES_USER", "root"),
    "password": os.getenv("POSTGRES_PASSWORD", "root"),
}

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

CREATE TABLE kbo.enterprise (
    enterprise_number VARCHAR(12) PRIMARY KEY,
    status VARCHAR(10),
    juridical_situation VARCHAR(10),
    type_of_enterprise VARCHAR(10),
    juridical_form VARCHAR(10),
    juridical_form_cac VARCHAR(10),
    start_date DATE
);

CREATE TABLE kbo.establishment (
    establishment_number VARCHAR(13) PRIMARY KEY,
    start_date DATE,
    enterprise_number VARCHAR(12)
);

CREATE TABLE kbo.denomination (
    entity_number VARCHAR(13),
    language VARCHAR(5),
    type_of_denomination VARCHAR(10),
    denomination TEXT
);

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

CREATE TABLE kbo.contact (
    entity_number VARCHAR(13),
    entity_contact VARCHAR(10),
    contact_type VARCHAR(20),
    value TEXT
);

CREATE TABLE kbo.activity (
    entity_number VARCHAR(13),
    activity_group VARCHAR(10),
    nace_version VARCHAR(10),
    nace_code VARCHAR(10),
    classification VARCHAR(10)
);

CREATE TABLE kbo.branch (
    id VARCHAR(13) PRIMARY KEY,
    start_date DATE,
    enterprise_number VARCHAR(12)
);

CREATE TABLE kbo.meta (
    variable VARCHAR(50) PRIMARY KEY,
    value TEXT
);

CREATE TABLE kbo.code (
    category VARCHAR(50),
    code VARCHAR(20),
    language VARCHAR(5),
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

TABLES_TO_LOAD = [
    ("enterprise.csv", "kbo.enterprise"),
    ("establishment.csv", "kbo.establishment"),
    ("denomination.csv", "kbo.denomination"),
    ("address.csv", "kbo.address"),
    ("contact.csv", "kbo.contact"),
    ("activity.csv", "kbo.activity"),
    ("branch.csv", "kbo.branch"),
    ("meta.csv", "kbo.meta"),
    ("code.csv", "kbo.code"),
]


def load_kbo_to_postgres(zip_path: Path) -> None:
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP archive not found at: {zip_path}")

    total_start = time.perf_counter()

    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            # KBO exports use DD-MM-YYYY dates; setting DMY lets Postgres
            # natively cast them into DATE columns during COPY.
            cur.execute("SET datestyle = 'DMY';")

            print("Creating schema and tables...")
            cur.execute(DDL_STATEMENTS)

            with zipfile.ZipFile(zip_path) as zf:
                for csv_name, table_name in TABLES_TO_LOAD:
                    t0 = time.perf_counter()
                    copy_sql = (
                        f"COPY {table_name} FROM STDIN "
                        "WITH (FORMAT csv, HEADER true, DELIMITER ',')"
                    )
                    with zf.open(csv_name) as csv_stream:
                        with cur.copy(copy_sql) as copy:
                            while chunk := csv_stream.read(1024 * 1024):
                                copy.write(chunk)

                    elapsed = time.perf_counter() - t0
                    print(f"Loaded {table_name:<18} ({cur.rowcount:,} rows) in {elapsed:.2f}s")

            print("Building indexes (this takes a few seconds)...")
            t0 = time.perf_counter()
            cur.execute(INDEX_STATEMENTS)
            print(f"Indexes created in {time.perf_counter() - t0:.2f}s")

        conn.commit()

    print(f"\nDone! Full load completed in {time.perf_counter() - total_start:.2f}s")


if __name__ == "__main__":
    archive_path = Path(__file__).resolve().parent / ZIP_FILENAME
    load_kbo_to_postgres(archive_path)