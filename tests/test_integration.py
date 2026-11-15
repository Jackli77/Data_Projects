"""Integration tests for the KBO ETL pipeline.

These tests load an archive into a real PostgreSQL database and verify the
data landed correctly. They work in two modes:

* CI: PostgreSQL is provided via GitHub Actions `services` (env vars
  POSTGRES_* are set). No Docker is started.
* Local: PostgreSQL is started via Docker Compose if Docker is available.

Tests are marked ``integration`` and skipped in the unit-test job.
"""

import subprocess
import time
from datetime import date
from pathlib import Path

import psycopg
import pytest

from kbo.load import load_kbo_to_postgres

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TEST_ARCHIVE = DATA_DIR / "KboOpenData_0544_2026_11_14_Update.zip"

EXPECTED_TABLES = [
    "kbo.enterprise",
    "kbo.establishment",
    "kbo.denomination",
    "kbo.address",
    "kbo.contact",
    "kbo.activity",
    "kbo.branch",
    "kbo.code",
]


def _docker_available() -> bool:
    """Return True if the `docker` CLI is reachable."""
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _compose_up() -> None:
    """Start PostgreSQL via Docker Compose and wait for it to accept connections."""
    subprocess.run(
        ["docker", "compose", "up", "-d", "pgdatabase"],
        capture_output=True,
        check=True,
        timeout=60,
    )
    # Wait up to 30 seconds for Postgres to be ready
    for _ in range(30):
        try:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "kbo-pgdatabase-1",
                    "pg_isready",
                    "-U",
                    "root",
                    "-d",
                    "kbo",
                ],
                capture_output=True,
                check=True,
                timeout=5,
            )
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            time.sleep(1)
    raise RuntimeError("PostgreSQL did not become ready within 30 seconds")


def _compose_down() -> None:
    """Stop the Docker Compose services."""
    subprocess.run(
        ["docker", "compose", "down"],
        capture_output=True,
        check=False,
        timeout=60,
    )


@pytest.fixture(scope="session")
def postgres():
    """Session-scoped PostgreSQL.

    In CI (GitHub Actions), Postgres is provided via the `services` block and
    environment variables. Locally, Docker Compose is used if available.
    """
    if not TEST_ARCHIVE.exists():
        pytest.skip(f"Test archive not found: {TEST_ARCHIVE}")

    pg_host = __import__("os").getenv("POSTGRES_HOST", "localhost")

    # CI mode: Postgres already running via services; just verify it's reachable
    if __import__("os").getenv("GITHUB_ACTIONS") == "true":
        for _ in range(30):
            try:
                with psycopg.connect(
                    host=pg_host,
                    port=int(__import__("os").getenv("POSTGRES_PORT", "5432")),
                    dbname=__import__("os").getenv("POSTGRES_DB", "kbo"),
                    user=__import__("os").getenv("POSTGRES_USER", "root"),
                    password=__import__("os").getenv("POSTGRES_PASSWORD", "root"),
                    connect_timeout=2,
                ):
                    return
            except psycopg.OperationalError:
                time.sleep(1)
        raise RuntimeError("PostgreSQL not reachable via GitHub Actions services")

    # Local mode: start Docker Compose
    if not _docker_available():
        pytest.skip("Docker is not available")

    _compose_up()
    yield
    _compose_down()


def _row_counts() -> dict[str, int]:
    import os

    counts: dict[str, int] = {}
    with psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "kbo"),
        user=os.getenv("POSTGRES_USER", "root"),
        password=os.getenv("POSTGRES_PASSWORD", "root"),
    ) as conn:
        with conn.cursor() as cur:
            for table in EXPECTED_TABLES:
                cur.execute(f"SELECT count(*) FROM {table};")
                counts[table] = cur.fetchone()[0]
    return counts


@pytest.mark.integration
def test_loads_update_archive(postgres):
    """Load the sample Update archive and verify all tables are populated."""
    load_kbo_to_postgres(
        datevar=date(2026, 11, 14),
        archive_type="Update",
        data_dir=str(DATA_DIR),
    )

    counts = _row_counts()

    # Every table should have data
    for table in EXPECTED_TABLES:
        assert counts[table] > 0, f"{table} is empty after load"

    # Sanity checks on known volumes (from the sample archive)
    assert counts["kbo.enterprise"] > 1_000_000
    assert counts["kbo.activity"] > 10_000_000
    assert counts["kbo.meta"] >= 0  # meta is refreshed, may be empty

    print("\nRow counts after integration load:")
    for table, count in sorted(counts.items()):
        print(f"  {table}: {count:,}")