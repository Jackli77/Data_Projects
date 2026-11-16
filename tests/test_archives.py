"""Archive names: reading them from the portal listing and finding them on disk."""

from datetime import date

import pytest

from kbo.extract import Archive, find_local_archives, parse_archive_filename, parse_listing

# Shaped like the portal's listing page. As on the real portal, the extract number does
# not follow from the date: 24 Oct is 522, and 25 Oct has two extracts (523 and 524).
LISTING_HTML = """
<table><tbody>
<tr><td>October 24, 2026</td>
<td><a href="files/KboOpenData_0522_2026_10_24_Full.zip">KboOpenData_0522_2026_10_24_Full.zip</a></td>
<td><a href="files/KboOpenData_0522_2026_10_24_Update.zip">KboOpenData_0522_2026_10_24_Update.zip</a></td></tr>
<tr><td>October 25, 2026</td>
<td><a href="files/KboOpenData_0524_2026_10_25_Full.zip">KboOpenData_0524_2026_10_25_Full.zip</a></td>
<td><a href="files/KboOpenData_0524_2026_10_25_Update.zip">KboOpenData_0524_2026_10_25_Update.zip</a></td></tr>
<tr><td>October 25, 2026</td>
<td><a href="files/KboOpenData_0523_2026_10_25_Full.zip">KboOpenData_0523_2026_10_25_Full.zip</a></td>
<td><a href="files/KboOpenData_0523_2026_10_25_Update.zip">KboOpenData_0523_2026_10_25_Update.zip</a></td></tr>
</tbody></table>
"""


def test_archive_knows_its_filename_and_url():
    archive = Archive(7, date(2025, 5, 26), "Full")

    assert archive.filename == "KboOpenData_0007_2025_05_26_Full.zip"
    assert archive.url.endswith("/affiliation/xml/files/KboOpenData_0007_2025_05_26_Full.zip")
    assert parse_archive_filename(archive.filename) == archive


@pytest.mark.parametrize(
    "name",
    [
        "KboOpenData_0544_2026_11_14_Update.zip.part",  # unfinished download
        "KboOpenData_0544_2026_11_14_Partial.zip",  # unknown type
        "KboOpenData_0544_2026_13_40_Update.zip",  # impossible date
        "something_else.zip",
    ],
)
def test_other_filenames_are_not_archives(name):
    assert parse_archive_filename(name) is None


def test_listing_gives_each_archive_once_in_extract_order():
    archives = parse_listing(LISTING_HTML)

    assert len(archives) == 6
    assert [a.number for a in archives if a.archive_type == "Update"] == [522, 523, 524]
    assert Archive(523, date(2026, 10, 25), "Update") in archives


def test_page_without_archives_gives_empty_listing():
    assert parse_listing("<html><body>Please log in</body></html>") == []


def test_local_archives_are_found_by_date_and_type(tmp_path):
    for name in (
        "KboOpenData_0524_2026_10_25_Update.zip",
        "KboOpenData_0523_2026_10_25_Update.zip",
        "KboOpenData_0523_2026_10_25_Full.zip",
        "KboOpenData_0522_2026_10_24_Update.zip",
        "KboOpenData_0525_2026_10_25_Update.zip.part",
    ):
        (tmp_path / name).touch()

    found = find_local_archives(tmp_path, date(2026, 10, 25), "Update")

    assert [a.number for a in found] == [523, 524]
    assert find_local_archives(tmp_path, date(2026, 10, 26), "Update") == []
    assert find_local_archives(tmp_path / "missing", date(2026, 10, 25), "Update") == []
