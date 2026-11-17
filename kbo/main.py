import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from kbo import extract
from kbo.extract import Archive, ArchiveType, VALID_ARCHIVE_TYPES, fetch_archives
from kbo.load import load_kbo_to_postgres
from kbo.runlog import logger as log, setup_logging


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}. Expected format: YYYY-MM-DD."
        ) from exc


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError(f"End date ({end}) cannot be earlier than start date ({start}).")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def run_pipeline(
    target_date: date,
    archive_type: ArchiveType = "Update",
    data_dir: Path = Path("data"),
    session: requests.Session | None = None,
    listing: list[Archive] | None = None,
) -> None:
    """Downloads (if needed) and loads the KBO archive(s) of one date into PostgreSQL.

    `session` and `listing` let a date range share one portal login and listing.
    """
    log.info("=== KBO Pipeline | %s | %s ===", f"{target_date:%Y-%m-%d}", archive_type)

    fetch_archives(
        target_date, archive_type=archive_type, data_dir=data_dir, session=session, listing=listing
    )

    load_kbo_to_postgres(
        datevar=target_date,
        archive_type=archive_type,
        data_dir=data_dir,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kbo",
        description="Run the KBO Open Data ETL pipeline (Download + Load to PostgreSQL)."
    )
    parser.add_argument(
        "-t",
        "--type",
        dest="archive_type",
        choices=VALID_ARCHIVE_TYPES,
        default="Update",
        help="Archive type to process: 'Full' or 'Update' (default: Update).",
    )
    parser.add_argument(
        "-d",
        "--date",
        type=parse_date,
        default=date.today(),
        help="Target archive date in YYYY-MM-DD format (default: today).",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        default=None,
        help="Optional end date (YYYY-MM-DD) to process a contiguous range of Update archives.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory where ZIP archives are stored (default: ./data).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.end_date:
        if args.archive_type != "Update":
            parser.error("--end-date can only be used with --type Update.")
        dates = date_range(args.date, args.end_date)
    else:
        dates = [args.date]

    setup_logging()

    # One login and one listing request for the whole run, however many dates it covers.
    session = extract.create_session_from_env()
    listing = extract.list_remote_archives(session)

    for current_date in dates:
        try:
            run_pipeline(
                target_date=current_date,
                archive_type=args.archive_type,
                data_dir=args.data_dir,
                session=session,
                listing=listing,
            )
        except FileNotFoundError as exc:
            # Expected, e.g. before the day's extract is published: a message, not a traceback.
            log.error("%s", exc)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()