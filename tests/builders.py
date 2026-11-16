"""Builds small KBO archives for tests.

Rows are dicts holding only the columns a test cares about; every other column is
left empty (NULL once loaded), e.g. ``{"EntityNumber": ENT_A, "Value": "info@a.be"}``.
"""

import csv
import io
import zipfile
from datetime import date
from pathlib import Path

from kbo.extract import Archive

Rows = list[dict[str, str]]

# CSV layout of the real archives.
HEADERS: dict[str, list[str]] = {
    "enterprise": [
        "EnterpriseNumber", "Status", "JuridicalSituation", "TypeOfEnterprise",
        "JuridicalForm", "JuridicalFormCAC", "StartDate",
    ],
    "establishment": ["EstablishmentNumber", "StartDate", "EnterpriseNumber"],
    "denomination": ["EntityNumber", "Language", "TypeOfDenomination", "Denomination"],
    "address": [
        "EntityNumber", "TypeOfAddress", "CountryNL", "CountryFR", "Zipcode",
        "MunicipalityNL", "MunicipalityFR", "StreetNL", "StreetFR", "HouseNumber",
        "Box", "ExtraAddressInfo", "DateStrikingOff",
    ],
    "contact": ["EntityNumber", "EntityContact", "ContactType", "Value"],
    "activity": ["EntityNumber", "ActivityGroup", "NaceVersion", "NaceCode", "Classification"],
    "branch": ["Id", "StartDate", "EnterpriseNumber"],
    "code": ["Category", "Code", "Language", "Description"],
    "meta": ["Variable", "Value"],
}
# Column that *_delete.csv is keyed on; every other table uses EntityNumber.
DELETE_KEYS = {"enterprise": "EnterpriseNumber", "establishment": "EstablishmentNumber", "branch": "Id"}

ENT_A = "0200.065.765"
ENT_B = "0200.068.636"
ENT_C = "0200.171.970"

FULL_NUMBER = 540
FULL_DATE = date(2026, 11, 10)
NEXT_DATE = date(2026, 11, 11)  # date of the update(s) that follow the Full

# What a Full load of `write_full()` puts in the database: enterprises A and B.
SEED: dict[str, Rows] = {
    "enterprise": [
        {"EnterpriseNumber": ENT_A, "Status": "AC", "StartDate": "09-08-1960"},
        {"EnterpriseNumber": ENT_B, "Status": "AC", "StartDate": "16-02-1923"},
    ],
    "establishment": [{"EstablishmentNumber": "2.000.000.339", "EnterpriseNumber": ENT_A}],
    "denomination": [
        {"EntityNumber": ENT_A, "Denomination": "Alpha"},
        {"EntityNumber": ENT_B, "Denomination": "Beta"},
    ],
    "address": [
        {"EntityNumber": ENT_A, "StreetNL": "Old Street"},
        {"EntityNumber": ENT_B, "StreetNL": "Markt"},
    ],
    "contact": [{"EntityNumber": ENT_A, "ContactType": "TEL", "Value": "09 000 00 00"}],
    "activity": [
        {"EntityNumber": ENT_A, "NaceCode": "84130"},
        {"EntityNumber": ENT_B, "NaceCode": "84130"},
    ],
    "branch": [],
    "code": [
        {"Category": "Status", "Code": "AC", "Language": "NL", "Description": "Actief"},
        {"Category": "Status", "Code": "AC", "Language": "FR", "Description": "Actif"},
    ],
}


def to_csv(columns: list[str], rows: Rows) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)  # rejects misspelled column names
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _meta(extract_type: str, number: int) -> str:
    rows = {"ExtractType": extract_type, "ExtractNumber": str(number), "Version": "1.0.0"}
    return to_csv(HEADERS["meta"], [{"Variable": k, "Value": v} for k, v in rows.items()])


def _write(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def write_full(data_dir: Path, number: int = FULL_NUMBER, day: date = FULL_DATE) -> Path:
    """Writes a Full archive containing SEED."""
    files = {f"{table}.csv": to_csv(HEADERS[table], rows) for table, rows in SEED.items()}
    files["meta.csv"] = _meta("full", number)
    return _write(Path(data_dir) / Archive(number, day, "Full").filename, files)


def write_update(
    data_dir: Path,
    number: int = FULL_NUMBER + 1,
    day: date = NEXT_DATE,
    *,
    deletes: dict[str, list[str]] | None = None,
    inserts: dict[str, Rows] | None = None,
    code: Rows | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    """Writes an Update archive laid out like the real ones.

    Every table gets a *_delete.csv (keys in `deletes`) and *_insert.csv (rows in
    `inserts`), except code, which ships complete as code.csv (`code`, default: SEED's).
    `files` replaces whole members verbatim, for malformed-input tests.
    """
    deletes, inserts = deletes or {}, inserts or {}
    members = {}
    for table in HEADERS:
        if table in ("code", "meta"):
            continue
        key = DELETE_KEYS.get(table, "EntityNumber")
        members[f"{table}_delete.csv"] = to_csv([key], [{key: k} for k in deletes.get(table, [])])
        members[f"{table}_insert.csv"] = to_csv(HEADERS[table], inserts.get(table, []))
    members["code.csv"] = to_csv(HEADERS["code"], SEED["code"] if code is None else code)
    members["meta.csv"] = _meta("update", number)
    members.update(files or {})
    return _write(Path(data_dir) / Archive(number, day, "Update").filename, members)
