import os
import zipfile
from datetime import date
from pathlib import Path
from typing import Literal
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

ArchiveType = Literal["Full", "Update"]
VALID_ARCHIVE_TYPES: tuple[ArchiveType, ...] = ("Full", "Update")

BASE_URL = "https://kbopub.economie.fgov.be/kbo-open-data"
LOGIN_URL = f"{BASE_URL}/static/j_spring_security_check"
FILES_URL = f"{BASE_URL}/affiliation/xml/files"


def _validate_archive_type(archive_type: str) -> None:
    if archive_type not in VALID_ARCHIVE_TYPES:
        raise ValueError(
            f"Invalid archive_type {archive_type!r}. Expected 'Full' or 'Update'."
        )


def create_authenticated_session(username: str, password: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/160.0"
    })

    session.get(f"{BASE_URL}/login")

    login_resp = session.post(
        LOGIN_URL,
        data={"j_username": username, "j_password": password},
    )
    login_resp.raise_for_status()

    if "/login" in login_resp.url:
        raise PermissionError("Login failed: check USERNAME and PASSWORD in your .env file.")

    return session


def archive_filename(datevar: date, archive_type: ArchiveType = "Update") -> str:
    _validate_archive_type(archive_type)
    start_date = date(2025, 5, 20)
    file_id = (datevar - start_date).days + 1
    return f"KboOpenData_{file_id:04d}_{datevar:%Y_%m_%d}_{archive_type}.zip"


def get_url(datevar: date, archive_type: ArchiveType = "Update") -> str:
    return f"{FILES_URL}/{archive_filename(datevar, archive_type=archive_type)}"


def download_zip(
    datevar: date,
    output_dir: Path | str = "data",
    archive_type: ArchiveType = "Update",
) -> Path:
    """Downloads the KBO ZIP archive ('Full' or 'Update') for the given date."""
    _validate_archive_type(archive_type)

    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    if not username or not password:
        raise ValueError("Missing USERNAME or PASSWORD environment variables.")

    session = create_authenticated_session(username, password)
    zip_url = get_url(datevar, archive_type=archive_type)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / zip_url.rsplit("/", 1)[-1]

    with session.get(zip_url, stream=True) as response:
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "zip" not in content_type and "octet-stream" not in content_type:
            raise ValueError(
                f"Expected a ZIP file from {response.url}, got {content_type}:\n{response.text[:500]}"
            )

        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

    return file_path