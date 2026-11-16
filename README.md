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

This installs the package in editable mode (dependencies are declared in `pyproject.toml`) together with the test tools, and provides the `kbo` command. `python -m kbo` is equivalent.

## 2. Configure credentials

Copy `.env.example` to `.env` in the project root and fill in your KBO login:

```ini
KBO_USERNAME=your_kbo_username
KBO_PASSWORD=your_kbo_password

# Optional PostgreSQL overrides (defaults shown)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=kbo
POSTGRES_USER=root
POSTGRES_PASSWORD=root
```

Variables already set in the real environment take precedence over `.env`.

## 3. Start PostgreSQL (Docker)

```bash
docker compose up -d
```

This starts:
* `postgres:18` on `localhost:5432` (user `root`, password `root`, db `kbo`)
* `pgadmin4` on `localhost:8085` (email `admin@admin.com`, password `root`)

## 4. Run the pipeline

Archive file names are looked up in the portal's file listing, so a normal run logs in to the portal even when the archive is already cached. The portal keeps roughly one month of archives.

### Single archive (default: today, type: Update)

```bash
kbo
```

### Specific date and type

```bash
# Full archive for a specific date
kbo --date 2026-11-10 --type Full

# Update archive for a specific date
kbo --date 2026-11-14 --type Update
```

### Date range (Update only)

```bash
kbo --date 2026-11-11 --end-date 2026-11-14 --type Update
```

### Custom data directory

```bash
kbo --date 2026-11-14 --type Update --data-dir ./my_downloads
```

An archive that is already in the data directory and intact is not downloaded again. To force a
fresh download, delete the ZIP first.

### CLI reference

```
usage: kbo [-h] [-t {Full,Update}] [-d YYYY-MM-DD]
           [--end-date YYYY-MM-DD] [--data-dir DIR]

optional arguments:
  -h, --help            show this help message and exit
  -t, --type {Full,Update}
                        Archive type to process: 'Full' or 'Update' (default: Update).
  -d, --date YYYY-MM-DD
                        Target archive date (default: today).
  --end-date YYYY-MM-DD
                        Optional end date to process a contiguous range of Update archives.
  --data-dir DIR        Directory where ZIP archives are stored (default: ./data).
```

## 5. Debugging helpers

### Preview CSV contents inside a ZIP

```bash
python -c "from kbo.helper import csv_preview; csv_preview('data/KboOpenData_0544_2026_11_14_Update.zip')"
```

## 6. Run tests

```bash
# Everything; the database tests are skipped (and listed as skipped) without PostgreSQL
python -m pytest

# Fast tests only: no network, no database
python -m pytest -m "not db"

# Database tests only
python -m pytest -m db
```

The database tests create one throwaway `kbo_test_<random>` database on the server from
`.env` / `POSTGRES_*` and drop it afterwards; the real `kbo` database is never touched.
No test contacts the KBO portal: a fake portal stands in for it.

| Test file | Covers | Needs |
|---|---|---|
| `tests/test_archives.py` | Archive names, reading the portal listing, finding cached archives | nothing |
| `tests/test_download.py` | All-or-nothing downloads, what gets downloaded for a date, credentials | nothing |
| `tests/test_cli.py` | The `kbo` command: dates, ranges, cached archives, invalid input | nothing |
| `tests/test_load_rules.py` | Update ordering rule, archive name vs. metadata, table columns matching the CSV headers | nothing |
| `tests/test_load_db.py` | Full load, Update deltas, refused updates and rollback, recovery after a partial failure, run history | PostgreSQL |

`tests/builders.py` writes the small archives the tests load, and `tests/conftest.py` holds
the fake portal and the throwaway database.

## 7. Docker

The image contains only the `kbo` package; archives live in the mounted `./data` folder and
credentials come from `.env`. Compose runs it on the same network as Postgres, which the
container reaches as `pgdatabase` (not `localhost`):

```bash
docker compose build kbo
docker compose run --rm kbo --date 2026-11-14 --type Update
```

The `kbo` service sits behind the `etl` profile, so a plain `docker compose up -d` still starts
only Postgres and pgAdmin.

## Expected behavior

* `Full` archives empty and refill all `kbo.*` tables and rebuild their indexes. The tables are
  truncated, not dropped, so views and other objects built on them survive a Full load.
* `Update` archives apply `_delete.csv` / `_insert.csv` deltas, replace `kbo.code`, and refresh `kbo.meta`.
* An `Update` is refused unless its extract number is exactly the database's current one + 1, so
  updates cannot be skipped or applied out of order. Updates the database already has are skipped,
  so a run that failed part-way can simply be repeated.
* An archive is refused if its `meta.csv` (extract number, Full/Update) contradicts its file name.
* Each archive loads in one transaction: a failed load leaves the database as it was.
* Downloaded ZIPs are cached under `./data` (or `--data-dir`) so re-runs skip the download. Every
  download is CRC-checked before it is accepted.
* A date range logs in to the portal once, not once per day.

Because tables are no longer dropped, a change to the table definitions in `kbo/load.py` is not
applied to an existing database by a Full load; drop the `kbo` tables by hand first.

## Logs and run history

The pipeline logs to stderr. Every line carries the ID of the load it belongs to:

```
2026-11-16 15:55:20 INFO    [2b243043] Updated kbo.contact        (-124 / +250 rows) in 0.01s
```

Each load attempt, successful or failed, is also recorded in `kbo.load_history`:

```sql
SELECT started_at, archive, status, extract_before, extract_after,
       rows_inserted, rows_deleted, error
FROM kbo.load_history
ORDER BY started_at DESC;
```

Failed runs keep the error message and the full traceback. After each load an integrity check
logs a warning if any establishment or branch points at an enterprise that does not exist.

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `PermissionError: Login failed` | Wrong KBO credentials | Update `KBO_USERNAME`/`KBO_PASSWORD` in `.env` |
| `FileNotFoundError: ... no Update archive for <date>` | The portal does not list that date and no ZIP for it is cached | The error names the date range the portal offers; pick a date in it |
| `UpdateSequenceError` | The update is not the next extract the database expects | Apply the missing updates first (the error names the next one), or run a `Full` load |
| `ArchiveMismatchError` | An archive's `meta.csv` contradicts its file name or the requested type | The file was renamed or is not what it claims; delete it from the data directory and run again to download it fresh |
| `psycopg.OperationalError` | PostgreSQL not running or wrong connection info | Start Postgres via `docker compose up -d` and check `POSTGRES_*` vars |
| `ModuleNotFoundError: dotenv` | Dependencies not installed | `pip install -r requirements.txt` |