import os
import zipfile
from datetime import date
from pathlib import Path
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

BASE_URL = "https://kbopub.economie.fgov.be/kbo-open-data"
LOGIN_URL = f"{BASE_URL}/static/j_spring_security_check"
FILES_URL = f"{BASE_URL}/affiliation/xml/files"


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


def get_url(datevar: date) -> str:
    start_date = date(2025, 5, 20)
    file_id = (datevar - start_date).days + 1
    return f"{FILES_URL}/KboOpenData_{file_id:04d}_{datevar:%Y_%m_%d}_Update.zip"


def download_zip(datevar: date, output_dir: Path | str = "data") -> Path:
    """Downloads the KBO update ZIP archive for the given date and saves it locally."""
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    if not username or not password:
        raise ValueError("Missing USERNAME or PASSWORD environment variables.")

    session = create_authenticated_session(username, password)
    zip_url = get_url(datevar)

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


def extract_csv_to_df(zip_path: Path | str, csv_name: str) -> pd.DataFrame:
    """Extracts a specific CSV file from a local KBO ZIP archive into a DataFrame."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        available_files = zf.namelist()
        matches = [
            name for name in available_files
            if name == csv_name or name.endswith(f"/{csv_name}")
        ]
        if not matches:
            raise KeyError(
                f"'{csv_name}' not found in '{zip_path}'. Available files: {available_files}"
            )

        with zf.open(matches[0]) as f:
            return pd.read_csv(f, encoding="utf-8", dtype=str)


if __name__ == "__main__":
    saved_zip = download_zip(date(2026, 11, 11), output_dir="downloads")
    print(f"Saved archive to: {saved_zip}")

    df_inserts = extract_csv_to_df(saved_zip, "enterprise_insert.csv")
    print(df_inserts.head())