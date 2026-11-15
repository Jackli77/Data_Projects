"""Tests for extract.py — pure functions only (no network, no DB)."""

import zipfile
from datetime import date

import pytest

from kbo.extract import ArchiveType, _validate_archive_type, archive_filename, get_url


def test_archive_filename_update(sample_date, expected_archive_filename):
    assert archive_filename(sample_date) == expected_archive_filename


def test_archive_filename_full(sample_date):
    result = archive_filename(sample_date, archive_type="Full")
    assert result == "KboOpenData_0544_2026_11_14_Full.zip"


def test_archive_filename_known_dates():
    # 2025-05-20 is day 1
    assert archive_filename(date(2025, 5, 20)) == "KboOpenData_0001_2025_05_20_Update.zip"
    # 2025-05-21 is day 2
    assert archive_filename(date(2025, 5, 21)) == "KboOpenData_0002_2025_05_21_Update.zip"


def test_archive_filename_invalid_type():
    with pytest.raises(ValueError, match="Invalid archive_type"):
        archive_filename(date(2026, 11, 14), archive_type="Invalid")


def test_validate_archive_type_valid():
    for at in ("Full", "Update"):
        _validate_archive_type(at)  # should not raise


def test_validate_archive_type_invalid():
    with pytest.raises(ValueError, match="Invalid archive_type"):
        _validate_archive_type("Full ")


def test_get_url_format(sample_date):
    url = get_url(sample_date)
    assert url.startswith("https://kbopub.economie.fgov.be/kbo-open-data/affiliation/xml/files/")
    assert url.endswith("KboOpenData_0544_2026_11_14_Update.zip")


def test_get_url_returns_str(sample_date):
    assert isinstance(get_url(sample_date), str)


def test_find_member_in_zip(sample_zip_bytes):
    from kbo.load import _find_member

    with zipfile.ZipFile(__import__("io").BytesIO(sample_zip_bytes)) as zf:
        assert _find_member(zf, "enterprise.csv") == "enterprise.csv"
        assert _find_member(zf, "enterprise_delete.csv") == "enterprise_delete.csv"
        assert _find_member(zf, "meta.csv") == "meta.csv"
        assert _find_member(zf, "nonexistent.csv") is None