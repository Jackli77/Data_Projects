import csv
import io

import pytest

from kbo.transformation import CsvCleaner, clean_header

from .builders import HEADERS


def clean(table: str, *rows: dict[str, str], header: list[str] | None = None) -> list[dict[str, str]]:
    """Runs rows (dicts of the columns that matter) through the cleaner.

    Returns the cleaned rows keyed by database column name. An empty string in the
    result is a field the loader will store as NULL.
    """
    header = header or HEADERS[table]
    raw = io.StringIO(newline="")
    writer = csv.DictWriter(raw, fieldnames=header, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(rows)

    cleaner = CsvCleaner(table, io.StringIO(raw.getvalue(), newline=""))
    output = "".join(cleaner.chunks())
    return [dict(zip(cleaner.columns, values)) for values in csv.reader(io.StringIO(output, newline=""))]


def values(rows: list[dict[str, str]], column: str) -> list[str]:
    return [row[column] for row in rows]


# --- column names -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "header, column",
    [
        ("EnterpriseNumber", "enterprise_number"),
        ("JuridicalFormCAC", "juridical_form_cac"),
        ("CountryNL", "country_nl"),
        ("Id", "id"),
        ("  Zipcode ", "zipcode"),
        ("﻿Variable", "variable"),  # byte-order mark on the first header
    ],
)
def test_headers_become_snake_case_column_names(header, column):
    assert clean_header([header]) == [column]


@pytest.mark.parametrize("header", ["Entity Number", "value; DROP TABLE kbo.meta", "1st", ""])
def test_headers_that_are_not_plain_names_are_rejected(header):
    with pytest.raises(ValueError, match="Unexpected column name"):
        clean_header([header])


# --- cleaning rules ---------------------------------------------------------------------


def test_empty_text_becomes_null():
    raw = '"EntityNumber","TypeOfAddress","Box","ExtraAddressInfo"\n"0200.065.765","REGO","",""\n'

    cleaner = CsvCleaner("address", io.StringIO(raw, newline=""))

    # Unquoted empty fields are what COPY reads as NULL (a quoted "" would be empty text).
    assert "".join(cleaner.chunks()) == "0200.065.765,REGO,,\n"


@pytest.mark.parametrize("zipcode", ["****", "*", "----", " ** "])
def test_placeholder_zipcode_becomes_null(zipcode):
    assert values(clean("address", {"EntityNumber": "1", "Zipcode": zipcode}), "zipcode") == [""]


def test_real_zipcodes_are_kept():
    rows = clean("address", {"EntityNumber": "1", "Zipcode": "9070"}, {"EntityNumber": "2", "Zipcode": "L-1234"})

    assert values(rows, "zipcode") == ["9070", "L-1234"]


def test_whitespace_in_free_text_is_tidied():
    rows = clean(
        "address",
        {"EntityNumber": "1", "StreetNL": "  Rue   de la  Loi ", "MunicipalityNL": "Brussel\t"},
    )

    assert rows[0]["street_nl"] == "Rue de la Loi"
    assert rows[0]["municipality_nl"] == "Brussel"


def test_denominations_keep_their_case_and_punctuation():
    rows = clean("denomination", {"EntityNumber": "1", "Denomination": " Café  DE Zwaan (NV) "})

    assert values(rows, "denomination") == ["Café DE Zwaan (NV)"]


@pytest.mark.parametrize(
    "contact_type, raw, cleaned",
    [
        ("EMAIL", " Info@Example.BE ", "info@example.be"),
        ("WEB", "WWW.Example.be/Contact", "www.example.be/contact"),
        ("TEL", " 089/32.39.50 ", "089/32.39.50"),  # only trimmed: phone formats are left alone
        ("FAX", "02 315  13 51", "02 315 13 51"),
    ],
)
def test_contact_values_are_normalised_by_type(contact_type, raw, cleaned):
    rows = clean("contact", {"EntityNumber": "1", "ContactType": contact_type, "Value": raw})

    assert values(rows, "value") == [cleaned]


def test_key_columns_are_never_changed():
    # Delete files refer to these values exactly as delivered.
    rows = clean("contact", {"EntityNumber": "0200.065.765", "EntityContact": "ENT", "ContactType": "TEL", "Value": "1"})

    assert rows[0]["entity_number"] == "0200.065.765"
    assert rows[0]["entity_contact"] == "ENT"


# --- duplicates -------------------------------------------------------------------------


def test_identical_rows_of_an_entity_are_dropped():
    tel = {"EntityNumber": "1", "ContactType": "TEL", "Value": "02 123"}
    other = {"EntityNumber": "1", "ContactType": "TEL", "Value": "02 999"}

    rows = clean("contact", tel, other, tel, tel)

    assert values(rows, "value") == ["02 123", "02 999"]


def test_rows_that_only_differ_before_cleaning_count_as_duplicates():
    rows = clean(
        "contact",
        {"EntityNumber": "1", "ContactType": "EMAIL", "Value": "Info@Example.be"},
        {"EntityNumber": "1", "ContactType": "EMAIL", "Value": " info@example.be"},
    )

    assert values(rows, "value") == ["info@example.be"]


def test_the_same_value_for_different_entities_is_not_a_duplicate():
    shared = {"ContactType": "TEL", "Value": "02 123"}

    rows = clean("contact", {"EntityNumber": "1", **shared}, {"EntityNumber": "2", **shared})

    assert values(rows, "entity_number") == ["1", "2"]


def test_cleaner_reports_how_many_rows_it_wrote_and_dropped():
    raw = "EntityNumber,ActivityGroup,NaceVersion,NaceCode,Classification\n" + "1,001,2025,84130,MAIN\n" * 3

    cleaner = CsvCleaner("activity", io.StringIO(raw, newline=""))
    list(cleaner.chunks())

    assert (cleaner.rows, cleaner.duplicates) == (1, 2)


# --- file shape -------------------------------------------------------------------------


def test_quoted_commas_and_line_breaks_survive():
    rows = clean("denomination", {"EntityNumber": "1", "Denomination": 'Dupont, "Fils" &\nCo'})

    # the line break is whitespace inside free text, so it is collapsed like any other
    assert values(rows, "denomination") == ['Dupont, "Fils" & Co']


def test_columns_follow_the_file_not_a_fixed_order():
    rows = clean("branch", {"Id": "9.000.000.001", "EnterpriseNumber": "1"}, header=["EnterpriseNumber", "Id"])

    assert rows == [{"enterprise_number": "1", "id": "9.000.000.001"}]


def test_row_with_the_wrong_number_of_fields_is_rejected():
    raw = "EntityNumber,ContactType,Value\n1,TEL\n"

    with pytest.raises(ValueError, match="line 2: expected 3 fields, got 2"):
        list(CsvCleaner("contact", io.StringIO(raw, newline="")).chunks())


def test_empty_file_yields_nothing():
    cleaner = CsvCleaner("contact", io.StringIO("", newline=""))

    assert cleaner.columns == []
    assert list(cleaner.chunks()) == []
