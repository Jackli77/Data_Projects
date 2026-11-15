# KBO Open Data ETL

Downloads Belgian KBO (Crossroads Bank for Enterprises) Open Data archives and loads them into PostgreSQL.

## Prerequisites

* Python 3.10+
* PostgreSQL 18
* A KBO Open Data account (username + password)

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. Configure credentials

Create a `.env` in the project root:

```ini
USERNAME=your_kbo_username
PASSWORD=your_kbo_password

# Optional PostgreSQL overrides (defaults shown)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=kbo
POSTGRES_USER=root
POSTGRES_PASSWORD=root
```

The example `.env` already contains `USERNAME` and `PASSWORD` for KBO authentication.

## 3. Start PostgreSQL (Docker)

```bash
docker-compose up -d
```

This starts:
* `postgres:18` on `localhost:5432` (user `root`, password `root`, db `kbo`)
* `pgadmin4` on `localhost:8085` (email `admin@admin.com`, password `root`)

## 4. Run the pipeline

Run from the project root so the `kbo` package is importable.

### Single archive (default: today, type: Update)

```bash
python kbo/main.py
```

### Specific date and type

```bash
# Full archive for a specific date
python kbo/main.py --date 2026-11-10 --type Full

# Update archive for a specific date
python kbo/main.py --date 2026-11-14 --type Update
```

### Date range (Update only)

```bash
python kbo/main.py --date 2026-11-11 --end-date 2026-11-14 --type Update
```

### Skip / force download

```bash
# Re-download even if the ZIP exists locally
python kbo/main.py --date 2026-11-14 --type Update --force-download

# Load from local disk only; fail if ZIP missing
python kbo/main.py --date 2026-11-14 --type Update --skip-download

# Custom data directory
python kbo/main.py --date 2026-11-14 --type Update --data-dir ./my_downloads
```

### CLI reference

```
usage: main.py [-h] [-t {Full,Update}] [-d YYYY-MM-DD]
               [--end-date YYYY-MM-DD] [--data-dir DIR]
               [--force-download] [--skip-download]

optional arguments:
  -h, --help            show this help message and exit
  -t, --type {Full,Update}
                        Archive type to process: 'Full' or 'Update' (default: Update).
  -d, --date YYYY-MM-DD
                        Target archive date (default: today).
  --end-date YYYY-MM-DD
                        Optional end date to process a contiguous range of Update archives.
  --data-dir DIR        Directory where ZIP archives are stored (default: ./data).
  --force-download      Re-download the ZIP archive even if it already exists locally.
  --skip-download       Only load from local disk; fail if the ZIP is missing.
```

## 5. Run modules directly

### Load an existing archive into Postgres

```bash
python kbo/load.py
```
Prompts for archive type and date, then loads from `./downloads`.

### Preview CSV contents inside a ZIP

```bash
python -c "from kbo.helper import csv_preview; csv_preview('data/KboOpenData_0544_2026_11_14_Update.zip')"
```

## 6. Run tests

Tests live in the `tests/` directory and cover pure functions (no network, no database required):

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_extract.py -v
```

| Test file | Covers |
|---|---|
| `tests/test_extract.py` | `archive_filename`, `get_url`, `_validate_archive_type`, `_find_member` |
| `tests/test_main.py` | `parse_date`, `date_range` |
| `tests/test_load.py` | `_to_snake_case` |
| `tests/test_helper.py` | `inspect_data` |

## 7. Docker

Build and run the image (entrypoint is `python kbo/main.py`):

```bash
docker build -t kbo .
docker run --env-file .env kbo
```

Note: the Dockerfile copies `KboOpenData_0540_2026_11_10_Full.zip` into the image; for live downloads you'll need network access to the KBO portal.

## Expected behavior

* `Full` archives recreate all `kbo.*` tables from scratch and build indexes.
* `Update` archives apply `_delete.csv` / `_insert.csv` deltas and refresh `kbo.meta`.
* Downloaded ZIPs are cached under `./data` (or `--data-dir`) so re-runs skip the download unless `--force-download` is set.
* Each run prints per-table timing (`-X / +Y rows in Zs`) and a total elapsed time.

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `PermissionError: Login failed` | Wrong KBO credentials | Update `USERNAME`/`PASSWORD` in `.env` |
| `FileNotFoundError: ZIP archive not found` | Missing local ZIP and download skipped/failed | Run without `--skip-download`, or check network/credentials |
| `psycopg.OperationalError` | PostgreSQL not running or wrong connection info | Start Postgres via `docker-compose up -d` and check `POSTGRES_*` vars |
| `ModuleNotFoundError: dotenv` | Dependencies not installed | `pip install -r requirements.txt` |