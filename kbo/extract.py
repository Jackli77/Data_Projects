import os
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Literal, NamedTuple
import requests
from dotenv import load_dotenv

from kbo.runlog import logger as log

# Real environment variables win over .env, so Docker/CI can inject their own values.
load_dotenv()

ArchiveType = Literal["Full", "Update"]
VALID_ARCHIVE_TYPES: tuple[ArchiveType, ...] = ("Full", "Update")

BASE_URL = "https://kbopub.economie.fgov.be/kbo-open-data"
LOGIN_URL = f"{BASE_URL}/static/j_spring_security_check"
LISTING_URL = f"{BASE_URL}/affiliation/xml/?files"
FILES_URL = f"{BASE_URL}/affiliation/xml/files"

# (connect, read) seconds. The read timeout is per chunk, not for the whole download.
REQUEST_TIMEOUT = (10, 60)


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

    session.get(f"{BASE_URL}/login", timeout=REQUEST_TIMEOUT)

    login_resp = session.post(
        LOGIN_URL,
        data={"j_username": username, "j_password": password},
        timeout=REQUEST_TIMEOUT,
    )
    login_resp.raise_for_status()

    if "/login" in login_resp.url:
        raise PermissionError("Login failed: check KBO_USERNAME and KBO_PASSWORD in your .env file.")

    return session


class Archive(NamedTuple):
    """One published KBO archive. Sorts by extract number, the order updates apply in."""

    number: int
    date: date
    archive_type: ArchiveType

    @property
    def filename(self) -> str:
        return f"KboOpenData_{self.number:04d}_{self.date:%Y_%m_%d}_{self.archive_type}.zip"

    @property
    def url(self) -> str:
        return f"{FILES_URL}/{self.filename}"


_ARCHIVE_RE = re.compile(r"KboOpenData_(\d+)_(\d{4})_(\d{2})_(\d{2})_(Full|Update)\.zip")


def parse_archive_filename(filename: str) -> Archive | None:
    match = _ARCHIVE_RE.fullmatch(filename)
    if not match:
        return None
    number, year, month, day, archive_type = match.groups()
    try:
        return Archive(int(number), date(int(year), int(month), int(day)), archive_type)
    except ValueError:
        return None


def parse_listing(html: str) -> list[Archive]:
    """Extracts every archive named in the portal's file listing page, oldest first."""
    found = {parse_archive_filename(m.group(0)) for m in _ARCHIVE_RE.finditer(html)}
    return sorted(a for a in found if a is not None)


def list_remote_archives(session: requests.Session) -> list[Archive]:
    """Reads which archives the portal currently offers.

    The extract number cannot be derived from the date: the portal sometimes skips a
    day and sometimes publishes two extracts on one date, so the listing is the only
    reliable source for file names.
    """
    response = session.get(LISTING_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    archives = parse_listing(response.text)
    if not archives:
        raise RuntimeError(f"No archives found in the KBO file listing at {LISTING_URL}.")
    return archives


def find_local_archives(
    data_dir: Path | str, datevar: date, archive_type: ArchiveType = "Update"
) -> list[Archive]:
    """Archives of the given date and type already present in `data_dir`, oldest first."""
    _validate_archive_type(archive_type)
    pattern = f"KboOpenData_*_{datevar:%Y_%m_%d}_{archive_type}.zip"
    found = (parse_archive_filename(p.name) for p in Path(data_dir).glob(pattern))
    return sorted(a for a in found if a is not None)


def is_valid_zip(path: Path | str, deep: bool = False) -> bool:
    """True if `path` is a complete ZIP archive.

    The ZIP directory sits at the end of the file, so a truncated download fails the
    quick check. `deep` also decompresses every member and verifies its CRC, which
    catches corruption in the middle of the file (a few seconds for a Full archive).
    """
    try:
        with zipfile.ZipFile(path) as zf:
            if not zf.namelist():
                return False
            return zf.testzip() is None if deep else True
    except (OSError, zipfile.BadZipFile):
        return False


def create_session_from_env() -> requests.Session:
    username = os.getenv("KBO_USERNAME")
    password = os.getenv("KBO_PASSWORD")
    if not username or not password:
        raise ValueError("Missing KBO_USERNAME or KBO_PASSWORD. Set them in .env or the environment.")
    return create_authenticated_session(username, password)


def download_archive(
    session: requests.Session, archive: Archive, output_dir: Path | str = "data"
) -> Path:
    """Downloads one archive into `output_dir` and returns its path."""
    zip_url = archive.url
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / archive.filename

    # Download to a .part file and only rename it once it is complete and valid, so an
    # interrupted run can never leave a truncated archive under the final name.
    part_path = file_path.with_name(file_path.name + ".part")
    try:
        with session.get(zip_url, stream=True, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "zip" not in content_type and "octet-stream" not in content_type:
                raise ValueError(
                    f"Expected a ZIP file from {response.url}, got {content_type}:\n{response.text[:500]}"
                )

            written = 0
            with open(part_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    written += len(chunk)

            # Content-Length describes the bytes on the wire, so it is only comparable
            # when the server did not apply a Content-Encoding.
            expected = response.headers.get("Content-Length")
            if (
                expected
                and expected.isdigit()
                and not response.headers.get("Content-Encoding")
                and written != int(expected)
            ):
                raise IOError(
                    f"Incomplete download of {file_path.name}: "
                    f"got {written:,} of {int(expected):,} bytes."
                )

        if not is_valid_zip(part_path, deep=True):
            raise IOError(f"Downloaded file is not a valid ZIP archive: {file_path.name}")

        os.replace(part_path, file_path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise

    return file_path


def fetch_archives(
    datevar: date,
    archive_type: ArchiveType = "Update",
    data_dir: Path | str = "data",
    session: requests.Session | None = None,
    listing: list[Archive] | None = None,
) -> list[Path]:
    """Makes sure every archive the portal lists for `datevar` is present in `data_dir`.

    Pass `session` and `listing` to reuse one login and one listing request across
    several dates; without them a new login is made.

    Returns the local paths in extract-number order. Archives that are already cached
    and valid are not downloaded again. A cached archive the
    portal no longer lists (it keeps roughly a month) is used as is.
    """
    _validate_archive_type(archive_type)
    if session is None:
        session = create_session_from_env()
    if listing is None:
        listing = list_remote_archives(session)

    remote = [a for a in listing if a.date == datevar and a.archive_type == archive_type]
    targets = sorted({*remote, *find_local_archives(data_dir, datevar, archive_type)})
    if not targets:
        raise FileNotFoundError(
            f"The KBO portal lists no {archive_type} archive for {datevar:%Y-%m-%d}. "
            f"It currently offers {listing[0].date:%Y-%m-%d} to {listing[-1].date:%Y-%m-%d}."
        )
    if archive_type == "Full":
        targets = targets[-1:]  # a Full replaces everything, only the newest matters

    paths = []
    for archive in targets:
        path = Path(data_dir) / archive.filename
        if archive in remote and not is_valid_zip(path):
            if path.exists():
                log.warning("Local archive is corrupt or incomplete, downloading again: %s", path)
            log.info("Downloading %s...", archive.filename)
            download_archive(session, archive, data_dir)
        elif not is_valid_zip(path):
            raise IOError(
                f"Local archive is corrupt and no longer offered by the portal: {path}"
            )
        else:
            log.info("Archive already exists locally, skipping download: %s", path)
        paths.append(path)
    return paths
