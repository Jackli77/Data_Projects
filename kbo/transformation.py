"""Transform step: turns the raw KBO CSV files into clean rows, between extract and load.

The loader streams every data CSV through `CsvCleaner` on its way into PostgreSQL:

* Header names become snake_case column names (EnterpriseNumber -> enterprise_number).
* Empty text becomes NULL, in every column. So do placeholder zipcodes such as '****'.
* Whitespace in free-text columns is tidied: trimmed, and runs of spaces collapsed.
* E-mail addresses and websites are lowercased.
* Rows that are identical in every column are dropped, in tables without a primary key.

Key columns (enterprise, establishment and entity numbers) are never changed, so the
*_delete.csv files of an Update archive keep matching the rows they refer to.
"""

import csv
import re
from collections.abc import Iterable, Iterator

# --- column names -----------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]*")


def to_snake_case(col_name: str) -> str:
    """Converts KBO PascalCase CSV headers (e.g. JuridicalFormCAC, CountryNL) to snake_case."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", col_name.strip()).lower()


def clean_header(raw_header: Iterable[str]) -> list[str]:
    """Turns a CSV header row into database column names."""
    columns = [to_snake_case(col.lstrip("﻿")) for col in raw_header]
    for col in columns:
        # The names are interpolated into SQL, so accept plain identifiers only.
        if not _IDENTIFIER_RE.fullmatch(col):
            raise ValueError(f"Unexpected column name {col!r} in CSV header.")
    return columns


# --- cleaning rules ---------------------------------------------------------------------

# Free-text columns, per table: whitespace is trimmed and runs of it collapsed to one space.
TEXT_COLUMNS: dict[str, set[str]] = {
    "denomination": {"denomination"},
    "address": {
        "country_nl", "country_fr", "zipcode", "municipality_nl", "municipality_fr",
        "street_nl", "street_fr", "house_number", "box", "extra_address_info",
    },
    "contact": {"value"},
    "code": {"description"},
}

# Columns where a value made only of these characters means "unknown" and becomes NULL.
PLACEHOLDER_COLUMNS: dict[str, set[str]] = {"address": {"zipcode"}}
PLACEHOLDER_CHARS = "*-?."

# contact.value is lowercased when contact.contact_type is one of these.
LOWERCASE_CONTACT_TYPES = {"EMAIL", "WEB"}

# Tables without a primary key, where KBO delivers some rows twice.
DEDUPLICATED_TABLES = {"denomination", "address", "contact", "activity"}

_ROWS_PER_CHUNK = 20_000


class CsvCleaner:
    """Reads one raw KBO CSV and yields it cleaned, as CSV text ready for COPY.

    `lines` is the raw file as text lines (open it with newline="" so quoted line
    breaks survive). The output has no header row; `columns` gives the column names.
    Empty fields are written unquoted, which COPY reads as NULL.

    `rows` and `duplicates` are complete once `chunks()` has been consumed.
    """

    def __init__(self, table: str, lines: Iterable[str]):
        self.table = table
        self.rows = 0  # rows written
        self.duplicates = 0  # rows dropped as exact duplicates
        self._reader = csv.reader(lines)
        self.columns = clean_header(next(self._reader, None) or [])
        self._clean_row = self._build_row_cleaner()

    def _build_row_cleaner(self):
        index = {col: i for i, col in enumerate(self.columns)}
        tidy = [index[c] for c in TEXT_COLUMNS.get(self.table, ()) if c in index]
        placeholders = [index[c] for c in PLACEHOLDER_COLUMNS.get(self.table, ()) if c in index]
        lowercase = (
            (index["contact_type"], index["value"])
            if self.table == "contact" and {"contact_type", "value"} <= index.keys()
            else None
        )

        def clean_row(row: list[str]) -> list[str]:
            for i in tidy:
                value = row[i]
                if value:
                    row[i] = " ".join(value.split())
            for i in placeholders:
                if row[i] and not row[i].strip(PLACEHOLDER_CHARS):
                    row[i] = ""
            if lowercase and row[lowercase[0]] in LOWERCASE_CONTACT_TYPES:
                row[lowercase[1]] = row[lowercase[1]].lower()
            return row

        return clean_row

    def _cleaned_rows(self) -> Iterator[list[str]]:
        width = len(self.columns)
        for row in self._reader:
            if len(row) != width:
                raise ValueError(
                    f"{self.table}.csv line {self._reader.line_num}: "
                    f"expected {width} fields, got {len(row)}."
                )
            yield self._clean_row(row)

    def _without_duplicates(self, rows: Iterable[list[str]]) -> Iterator[list[str]]:
        """Drops rows identical to an earlier row of the same entity.

        KBO files list all rows of an entity together, so only the current entity's
        rows need remembering; that keeps memory flat for the 34-million-row activity
        file. Rows are compared after cleaning, so 'Info@X.be' and 'info@x.be' count
        as the same e-mail address.
        """
        entity, seen = None, set()
        for row in rows:
            if row[0] != entity:
                entity = row[0]
                seen.clear()
            key = tuple(row)
            if key in seen:
                self.duplicates += 1
                continue
            seen.add(key)
            yield row

    def chunks(self) -> Iterator[str]:
        """The cleaned file as CSV text, in chunks of a few thousand rows."""
        if not self.columns:
            return
        rows = self._cleaned_rows()
        if self.table in DEDUPLICATED_TABLES:
            rows = self._without_duplicates(rows)

        buffer = _Buffer()
        writer = csv.writer(buffer, lineterminator="\n")
        batch = []
        for row in rows:
            batch.append(row)
            if len(batch) >= _ROWS_PER_CHUNK:
                writer.writerows(batch)
                self.rows += len(batch)
                batch.clear()
                yield buffer.take()
        if batch:
            writer.writerows(batch)
            self.rows += len(batch)
            yield buffer.take()


class _Buffer:
    """Minimal file-like target for csv.writer that hands its contents over in one piece."""

    def __init__(self):
        self._parts: list[str] = []

    def write(self, text: str) -> None:
        self._parts.append(text)

    def take(self) -> str:
        text = "".join(self._parts)
        self._parts.clear()
        return text
