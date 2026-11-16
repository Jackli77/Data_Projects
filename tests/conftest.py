"""Shared fixtures: a fake KBO portal (no network) and a throwaway PostgreSQL database."""

import io
import uuid
import zipfile

import psycopg
import pytest
import requests

from kbo import extract, load
from kbo.extract import LISTING_URL, Archive


@pytest.fixture(scope="session")
def zip_bytes() -> bytes:
    """A small, valid ZIP file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("meta.csv", "Variable,Value\nExtractNumber,1\n")
    return buf.getvalue()


# --- fake portal ----------------------------------------------------------------------


class _Response:
    def __init__(self, body=b"", text="", content_length=None, drop_after=None):
        self.body, self.text, self.drop_after = body, text, drop_after
        self.url = "https://portal.invalid/"
        self.headers = {"Content-Type": "application/zip"}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), 16):
            if self.drop_after is not None and start >= self.drop_after:
                raise requests.exceptions.ChunkedEncodingError("connection broken")
            yield self.body[start : start + 16]


class FakePortal:
    """Stands in for the logged-in session: lists and serves whatever was published."""

    def __init__(self, default_body: bytes):
        self._default_body = default_body
        self._files: dict[str, _Response] = {}
        self.downloaded: list[str] = []
        self.timeouts: list = []

    def publish(self, *archives: Archive, body=None, content_length="actual", drop_after=None):
        """Offers archives for download. `body`, `content_length` and `drop_after`
        simulate a broken transfer; by default a valid ZIP is served completely."""
        body = self._default_body if body is None else body
        if content_length == "actual":
            content_length = len(body)
        for archive in archives:
            self._files[archive.filename] = _Response(body, "", content_length, drop_after)

    def get(self, url, timeout=None, **kwargs):
        self.timeouts.append(timeout)
        if url == LISTING_URL:
            links = "".join(f'<a href="files/{name}">{name}</a>' for name in self._files)
            return _Response(text=f"<html><table>{links}</table></html>")
        name = url.rsplit("/", 1)[-1]
        self.downloaded.append(name)
        return self._files[name]


@pytest.fixture
def portal(monkeypatch, zip_bytes) -> FakePortal:
    """The KBO portal, with nothing published yet. Replaces the real login."""
    fake = FakePortal(zip_bytes)
    monkeypatch.setattr(extract, "create_session_from_env", lambda: fake)
    return fake


# --- throwaway database ---------------------------------------------------------------


class Database:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def reset(self) -> None:
        self._conn.execute("DROP SCHEMA IF EXISTS kbo CASCADE")

    def query(self, sql: str) -> list[tuple]:
        return self._conn.execute(sql).fetchall()

    def column(self, sql: str) -> list:
        """First column of the result."""
        return [row[0] for row in self.query(sql)]

    def counts(self) -> dict[str, int]:
        tables = self.column(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'kbo' ORDER BY 1"
        )
        return {t: self.column(f"SELECT count(*) FROM kbo.{t}")[0] for t in tables}

    @property
    def extract_number(self) -> int:
        return int(self.column("SELECT value FROM kbo.meta WHERE variable = 'ExtractNumber'")[0])


@pytest.fixture(scope="session")
def _test_database():
    """Creates one `kbo_test_<random>` database for the whole run and drops it afterwards.

    kbo.load is pointed at it, so the real `kbo` database is never touched.
    """
    name = f"kbo_test_{uuid.uuid4().hex[:12]}"
    try:
        admin = psycopg.connect(**load.DB_CONFIG, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable ({str(exc).strip().splitlines()[-1]})")

    with admin, pytest.MonkeyPatch.context() as patch:
        admin.execute(f'CREATE DATABASE "{name}"')
        patch.setitem(load.DB_CONFIG, "dbname", name)
        try:
            with psycopg.connect(**load.DB_CONFIG, autocommit=True) as conn:
                yield Database(conn)
        finally:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def db(_test_database) -> Database:
    """An empty database: no kbo schema yet, as before the first Full load."""
    _test_database.reset()
    return _test_database
