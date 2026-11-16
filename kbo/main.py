import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from kbo.extract import ArchiveType, VALID_ARCHIVE_TYPES, fetch_archives, find_local_archives
from kbo.load import load_kbo_to_postgres


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
    force_download: bool = False,
    skip_download: bool = False,
) -> None:
    """Downloads (if needed) and loads the KBO archive(s) of one date into PostgreSQL."""
    print(f"=== KBO Pipeline | {target_date:%Y-%m-%d} | {archive_type} ===")

    if skip_download:
        local = find_local_archives(data_dir, target_date, archive_type)
        if not local:
            raise FileNotFoundError(
                f"--skip-download set, but no {archive_type} archive for "
                f"{target_date:%Y-%m-%d} found in: {data_dir}"
            )
        for archive in local:
            print(f"Using existing local archive: {data_dir / archive.filename}")
    else:
        fetch_archives(target_date, archive_type=archive_type, data_dir=data_dir, force=force_download)

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
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download the ZIP archive even if it already exists locally.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Only load from local disk; fail if the ZIP is missing.",
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

    for current_date in dates:
        run_pipeline(
            target_date=current_date,
            archive_type=args.archive_type,
            data_dir=args.data_dir,
            force_download=args.force_download,
            skip_download=args.skip_download,
        )


if __name__ == "__main__":
    main()