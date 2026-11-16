"""Downloading archives from the portal into the local cache (fake portal, no network)."""

from datetime import date

import pytest
import requests

from kbo import extract
from kbo.extract import REQUEST_TIMEOUT, Archive, download_archive, fetch_archives, is_valid_zip

OCT_24 = date(2026, 10, 24)
OCT_25 = date(2026, 10, 25)
U522 = Archive(522, OCT_24, "Update")
U523 = Archive(523, OCT_25, "Update")
U524 = Archive(524, OCT_25, "Update")
F523 = Archive(523, OCT_25, "Full")
F524 = Archive(524, OCT_25, "Full")


def files_in(directory) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


# --- a single download is all-or-nothing -----------------------------------------------


def test_download_saves_the_archive_under_its_name(portal, tmp_path, zip_bytes):
    portal.publish(U523)

    path = download_archive(portal, U523, tmp_path)

    assert path == tmp_path / U523.filename
    assert path.read_bytes() == zip_bytes
    assert files_in(tmp_path) == [U523.filename]  # no .part file left behind
    assert portal.timeouts == [REQUEST_TIMEOUT]


@pytest.mark.parametrize(
    "broken_transfer, error",
    [
        pytest.param({"content_length": 10_000}, "Incomplete download", id="shorter-than-announced"),
        pytest.param({"content_length": None, "body": b"PK\x03\x04 cut off"}, "not a valid ZIP", id="truncated-zip"),
        pytest.param({"drop_after": 32}, "connection broken", id="connection-dropped"),
    ],
)
def test_failed_download_leaves_no_file(portal, tmp_path, broken_transfer, error):
    portal.publish(U523, **broken_transfer)

    with pytest.raises((IOError, requests.RequestException), match=error):
        download_archive(portal, U523, tmp_path)

    assert files_in(tmp_path) == []


def test_download_with_corrupt_contents_is_rejected(portal, tmp_path, zip_bytes):
    # The ZIP directory is intact, so only a CRC check of the contents notices this.
    damaged = zip_bytes.replace(b"ExtractNumber", b"ExtractNumbe!", 1)
    assert damaged != zip_bytes and is_valid_zip_bytes(tmp_path, damaged)
    portal.publish(U523, body=damaged)

    with pytest.raises(IOError, match="not a valid ZIP"):
        download_archive(portal, U523, tmp_path)

    assert files_in(tmp_path) == []


def is_valid_zip_bytes(tmp_path, body: bytes) -> bool:
    """Whether the quick (non-CRC) check accepts these bytes."""
    probe = tmp_path.parent / "probe.zip"
    probe.write_bytes(body)
    return is_valid_zip(probe)


def test_failed_redownload_keeps_the_existing_archive(portal, tmp_path, zip_bytes):
    existing = tmp_path / U523.filename
    existing.write_bytes(zip_bytes)
    portal.publish(U523, drop_after=32)

    with pytest.raises(requests.RequestException):
        download_archive(portal, U523, tmp_path)

    assert existing.read_bytes() == zip_bytes
    assert files_in(tmp_path) == [U523.filename]


# --- fetch_archives: what to download for a date ---------------------------------------


def test_fetch_takes_the_number_from_the_listing_not_the_date(portal, tmp_path):
    # Counting days since the first extract would give 523 for 24 Oct; the portal says 522.
    portal.publish(U522, U523, U524)

    paths = fetch_archives(OCT_24, data_dir=tmp_path)

    assert [p.name for p in paths] == [U522.filename]
    assert portal.downloaded == [U522.filename]


def test_fetch_gets_every_update_of_a_date_in_extract_order(portal, tmp_path):
    portal.publish(U524, U523, U522)

    paths = fetch_archives(OCT_25, data_dir=tmp_path)

    assert [p.name for p in paths] == [U523.filename, U524.filename]
    assert files_in(tmp_path) == [U523.filename, U524.filename]


def test_fetch_gets_only_the_newest_full_of_a_date(portal, tmp_path):
    portal.publish(F523, F524, U523, U524)

    paths = fetch_archives(OCT_25, archive_type="Full", data_dir=tmp_path)

    assert [p.name for p in paths] == [F524.filename]


def test_fetch_downloads_only_what_is_missing_or_corrupt(portal, tmp_path, zip_bytes):
    portal.publish(U523, U524)
    (tmp_path / U523.filename).write_bytes(zip_bytes)
    (tmp_path / U524.filename).write_bytes(zip_bytes[:-10])  # interrupted earlier

    fetch_archives(OCT_25, data_dir=tmp_path)

    assert portal.downloaded == [U524.filename]
    assert is_valid_zip(tmp_path / U524.filename)


def test_fetch_uses_a_cached_archive_the_portal_no_longer_offers(portal, tmp_path, zip_bytes):
    portal.publish(U523)
    old = Archive(400, date(2026, 6, 23), "Update")
    (tmp_path / old.filename).write_bytes(zip_bytes)

    paths = fetch_archives(old.date, data_dir=tmp_path)

    assert [p.name for p in paths] == [old.filename]
    assert portal.downloaded == []


def test_fetch_for_an_unlisted_date_says_what_is_available(portal, tmp_path):
    portal.publish(U522, U524)

    with pytest.raises(FileNotFoundError, match="2026-10-24 to 2026-10-25"):
        fetch_archives(date(2026, 10, 26), data_dir=tmp_path)


def test_fetch_fails_clearly_when_the_listing_shows_no_archives(portal, tmp_path):
    with pytest.raises(RuntimeError, match="No archives found"):
        fetch_archives(OCT_25, data_dir=tmp_path)


# --- credentials ----------------------------------------------------------------------


@pytest.fixture
def logins(monkeypatch) -> list[tuple[str, str]]:
    """Records login attempts instead of contacting the portal."""
    attempts = []
    monkeypatch.setattr(extract, "create_authenticated_session", lambda u, p: attempts.append((u, p)))
    # Windows always sets USERNAME to the OS login; it must never be sent to the portal.
    monkeypatch.setenv("USERNAME", "os-login")
    monkeypatch.setenv("PASSWORD", "unrelated")
    return attempts


def test_login_uses_the_kbo_credentials(logins, monkeypatch):
    monkeypatch.setenv("KBO_USERNAME", "kbo-user")
    monkeypatch.setenv("KBO_PASSWORD", "kbo-pass")

    extract.create_session_from_env()

    assert logins == [("kbo-user", "kbo-pass")]


def test_no_login_is_attempted_without_kbo_credentials(logins, monkeypatch):
    monkeypatch.delenv("KBO_USERNAME", raising=False)
    monkeypatch.delenv("KBO_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="KBO_USERNAME"):
        extract.create_session_from_env()

    assert logins == []
