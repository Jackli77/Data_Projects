"""Pytest fixtures for the KBO ETL test suite."""

import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from kbo.extract import archive_filename, get_url


@pytest.fixture
def sample_date() -> date:
    return date(2026, 11, 14)


@pytest.fixture
def expected_archive_filename() -> str:
    return "KboOpenData_0544_2026_11_14_Update.zip"


@pytest.fixture
def sample_zip_bytes() -> bytes:
    """Create an in-memory ZIP with two small CSV members."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("enterprise.csv", "EnterpriseNumber,Status\n0200.065.765,AC\n")
        zf.writestr("enterprise_delete.csv", "EnterpriseNumber\n0200.065.765\n")
        zf.writestr("enterprise_insert.csv", "EnterpriseNumber,Status\n0200.066.765,AC\n")
        zf.writestr("meta.csv", "Variable,Value\nSnapshotDate,14-11-2026\n")
    return buf.getvalue()


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "enterprise_number": ["0200.065.765", "0200.068.636"],
            "status": ["AC", "AC"],
        }
    )