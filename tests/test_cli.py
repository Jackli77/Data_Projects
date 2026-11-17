"""The `kbo` command: what it downloads and loads for a given command line."""

from datetime import date

import pytest

from kbo import main
from kbo.extract import Archive

NOV_11 = date(2026, 11, 11)
U541 = Archive(541, NOV_11, "Update")


@pytest.fixture
def loads(monkeypatch) -> list[tuple[date, str]]:
    """Records (date, type) of every load instead of touching a database."""
    calls = []
    monkeypatch.setattr(
        main, "load_kbo_to_postgres", lambda datevar, archive_type, data_dir: calls.append((datevar, archive_type))
    )
    return calls


def kbo(tmp_path, *args: str) -> None:
    main.main(["--data-dir", str(tmp_path), *args])


def test_downloads_then_loads_the_requested_date(portal, loads, tmp_path):
    portal.publish(U541)

    kbo(tmp_path, "--date", "2026-11-11")

    assert portal.downloaded == [U541.filename]
    assert loads == [(NOV_11, "Update")]


def test_defaults_to_todays_update(portal, loads, tmp_path):
    portal.publish(Archive(900, date.today(), "Update"))

    kbo(tmp_path)

    assert loads == [(date.today(), "Update")]


def test_full_load(portal, loads, tmp_path):
    portal.publish(Archive(541, NOV_11, "Full"))

    kbo(tmp_path, "--date", "2026-11-11", "--type", "Full")

    assert loads == [(NOV_11, "Full")]


def test_date_range_processes_each_day_in_order(portal, loads, tmp_path):
    days = [date(2026, 11, d) for d in (11, 12, 13)]
    portal.publish(*(Archive(530 + d.day, d, "Update") for d in days))

    kbo(tmp_path, "--date", "2026-11-11", "--end-date", "2026-11-13")

    assert loads == [(d, "Update") for d in days]
    # one login and one listing request for the whole range, not one per day
    assert (portal.logins, portal.listing_requests) == (1, 1)


def test_nothing_is_loaded_when_the_download_fails(portal, loads, tmp_path):
    portal.publish(U541, drop_after=0)

    with pytest.raises(Exception, match="connection broken"):
        kbo(tmp_path, "--date", "2026-11-11")

    assert loads == []


def test_cached_archive_is_loaded_without_downloading_it_again(portal, loads, tmp_path, zip_bytes):
    portal.publish(U541)
    (tmp_path / U541.filename).write_bytes(zip_bytes)

    kbo(tmp_path, "--date", "2026-11-11")

    assert portal.downloaded == []
    assert loads == [(NOV_11, "Update")]


@pytest.mark.parametrize(
    "args, message",
    [
        pytest.param(["--date", "11-11-2026"], "Expected format: YYYY-MM-DD", id="date-format"),
        pytest.param(["--date", "2026-13-01"], "Invalid date", id="impossible-date"),
        pytest.param(["--type", "Delta"], "invalid choice", id="unknown-type"),
        pytest.param(
            ["--type", "Full", "--date", "2026-11-11", "--end-date", "2026-11-12"],
            "--end-date can only be used with --type Update",
            id="range-of-fulls",
        ),
    ],
)
def test_invalid_command_line_is_rejected_before_anything_runs(args, message, loads, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        kbo(tmp_path, *args)

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert loads == []


def test_unpublished_archive_ends_with_an_error_not_a_traceback(portal, loads, tmp_path):
    portal.publish(U541)

    with pytest.raises(SystemExit) as exit_info:
        kbo(tmp_path, "--date", "2026-11-12")

    assert exit_info.value.code == 1
    assert loads == []


def test_end_date_before_start_date_is_rejected(loads, tmp_path):
    with pytest.raises(ValueError, match="cannot be earlier"):
        kbo(tmp_path, "--date", "2026-11-12", "--end-date", "2026-11-11")

    assert loads == []
