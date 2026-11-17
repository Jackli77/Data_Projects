"""The data warehouse (star schema `dwh`) built from the loaded kbo tables. Needs PostgreSQL."""

from datetime import date

import psycopg
import pytest

from kbo.load import _connect, load_kbo_to_postgres
from kbo.warehouse import DIMENSIONS, refresh_warehouse

from .builders import FULL_DATE, FULL_NUMBER, NEXT_DATE, write_full, write_update

pytestmark = pytest.mark.db

COMPANY = "0200.065.765"  # legal person in Destelbergen, one establishment in Brussels
PERSON = "0200.068.636"  # natural person: no address, no juridical form, no activity
NEWCOMER = "0200.171.970"
SHOP = "2.000.000.339"


def label(category: str, code: str, nl: str, fr: str) -> list[dict[str, str]]:
    return [
        {"Category": category, "Code": code, "Language": "NL", "Description": nl},
        {"Category": category, "Code": code, "Language": "FR", "Description": fr},
    ]


SOURCE = {
    "enterprise": [
        {"EnterpriseNumber": COMPANY, "Status": "AC", "JuridicalSituation": "000",
         "TypeOfEnterprise": "2", "JuridicalForm": "416", "StartDate": "09-08-1960"},
        {"EnterpriseNumber": PERSON, "Status": "AC", "JuridicalSituation": "000",
         "TypeOfEnterprise": "1", "StartDate": "16-02-2020"},
    ],
    "establishment": [{"EstablishmentNumber": SHOP, "StartDate": "01-11-1974", "EnterpriseNumber": COMPANY}],
    "denomination": [
        {"EntityNumber": COMPANY, "Language": "1", "TypeOfDenomination": "001", "Denomination": "Alpha SA"},
        {"EntityNumber": COMPANY, "Language": "2", "TypeOfDenomination": "001", "Denomination": "Alpha NV"},
        {"EntityNumber": COMPANY, "Language": "2", "TypeOfDenomination": "003", "Denomination": "Alpha Shop"},
        {"EntityNumber": PERSON, "Language": "1", "TypeOfDenomination": "001", "Denomination": "Dupont"},
    ],
    "address": [
        {"EntityNumber": COMPANY, "TypeOfAddress": "REGO", "Zipcode": "9070",
         "MunicipalityNL": "Destelbergen", "MunicipalityFR": "Destelbergen"},
        {"EntityNumber": SHOP, "TypeOfAddress": "BAET", "Zipcode": "1000",
         "MunicipalityNL": "Brussel", "MunicipalityFR": "Bruxelles"},
    ],
    "contact": [
        {"EntityNumber": COMPANY, "EntityContact": "ENT", "ContactType": "EMAIL", "Value": "info@alpha.example"},
        {"EntityNumber": COMPANY, "EntityContact": "ENT", "ContactType": "TEL", "Value": "09 000 00 00"},
        {"EntityNumber": SHOP, "EntityContact": "EST", "ContactType": "WEB", "Value": "shop.alpha.example"},
    ],
    "activity": [
        {"EntityNumber": COMPANY, "ActivityGroup": "001", "NaceVersion": "2025", "NaceCode": "8413001", "Classification": "MAIN"},
        {"EntityNumber": COMPANY, "ActivityGroup": "001", "NaceVersion": "2025", "NaceCode": "47110", "Classification": "SECO"},
        {"EntityNumber": COMPANY, "ActivityGroup": "001", "NaceVersion": "2008", "NaceCode": "99999", "Classification": "MAIN"},
        {"EntityNumber": SHOP, "ActivityGroup": "001", "NaceVersion": "2025", "NaceCode": "47110", "Classification": "MAIN"},
    ],
    "code": [
        *label("JuridicalForm", "416", "Coöperatieve vennootschap", "Société coopérative"),
        *label("JuridicalSituation", "000", "Normale toestand", "Situation normale"),
        *label("TypeOfEnterprise", "1", "Natuurlijk persoon", "Personne physique"),
        *label("TypeOfEnterprise", "2", "Rechtspersoon", "Personne morale"),
        *label("Nace2025", "84", "Openbaar bestuur", "Administration publique"),
        *label("Nace2025", "841", "Openbaar bestuur (groep)", "Administration (groupe)"),
        *label("Nace2025", "8413", "Openbaar bestuur (klasse)", "Administration (classe)"),
        *label("Nace2025", "84130", "Bestuur bedrijfsleven", "Administration des entreprises"),
        *label("Nace2025", "47110", "Supermarkten", "Supermarchés"),
    ],
}


@pytest.fixture
def warehouse(db, tmp_path):
    """A database loaded with SOURCE, with the warehouse built from it."""
    write_full(tmp_path, tables=SOURCE)
    load_kbo_to_postgres(FULL_DATE, archive_type="Full", data_dir=tmp_path)
    refresh(db)
    return tmp_path


def refresh(db) -> dict[str, int]:
    with _connect() as conn:
        return refresh_warehouse(conn)


def enterprise_fact(db, enterprise_number: str, columns: str) -> tuple:
    """Columns of the star for one enterprise, with every dimension joined in."""
    return db.query(
        f"""
        SELECT {columns}
        FROM dwh.fact_enterprise f
        JOIN dwh.dim_enterprise e USING (enterprise_key)
        JOIN dwh.dim_date d ON d.date_key = f.start_date_key
        JOIN dwh.dim_geography g USING (geography_key)
        JOIN dwh.dim_activity a ON a.activity_key = f.main_activity_key
        JOIN dwh.dim_juridical_form jf USING (juridical_form_key)
        JOIN dwh.dim_juridical_situation js USING (juridical_situation_key)
        WHERE e.enterprise_number = '{enterprise_number}'
        """
    )[0]


# --- grain ------------------------------------------------------------------------------


def test_one_fact_row_per_enterprise_and_per_establishment(warehouse, db):
    assert db.column("SELECT count(*) FROM dwh.fact_enterprise") == [2]
    assert db.column("SELECT sum(enterprise_count) FROM dwh.fact_enterprise") == [2]
    assert db.column("SELECT establishment_number FROM dwh.fact_establishment") == [SHOP]


# --- dimensions resolve to readable attributes --------------------------------------------


def test_enterprise_is_described_by_its_dimensions(warehouse, db):
    assert enterprise_fact(
        db, COMPANY, "e.name, e.enterprise_type_fr, jf.label_fr, js.label_nl, d.full_date, d.year, d.decade"
    ) == ("Alpha NV", "Personne morale", "Société coopérative", "Normale toestand", date(1960, 8, 9), 1960, 1960)


def test_registered_office_gives_province_and_region(warehouse, db):
    assert enterprise_fact(db, COMPANY, "g.zipcode, g.municipality_nl, g.province, g.region, g.is_belgium") == (
        "9070", "Destelbergen", "East Flanders", "Flanders", True,
    )


def test_main_activity_carries_the_nace_hierarchy(warehouse, db):
    # the source row has the 7-digit code 8413001; the dimension is at 5-digit level
    assert enterprise_fact(db, COMPANY, "a.nace_code, a.label_nl, a.class_code, a.group_code, a.division_code, a.division_label_fr") == (
        "84130", "Bestuur bedrijfsleven", "8413", "841", "84", "Administration publique",
    )


def test_measures(warehouse, db):
    assert enterprise_fact(db, COMPANY, "f.establishment_count, f.has_email, f.has_phone, f.has_website, f.age_years") == (
        1, 1, 1, 0, 66,
    )


def test_establishment_has_its_own_place_and_activity(warehouse, db):
    assert db.query(
        """
        SELECT e.enterprise_number, g.region, a.nace_code, d.year
        FROM dwh.fact_establishment f
        JOIN dwh.dim_enterprise e USING (enterprise_key)
        JOIN dwh.dim_geography g USING (geography_key)
        JOIN dwh.dim_activity a ON a.activity_key = f.main_activity_key
        JOIN dwh.dim_date d ON d.date_key = f.start_date_key
        """
    ) == [(COMPANY, "Brussels", "47110", 1974)]


# --- unknown members ----------------------------------------------------------------------


def test_missing_attributes_point_to_the_unknown_member(warehouse, db):
    # A natural person has no address, juridical form or activity in the open data.
    assert enterprise_fact(db, PERSON, "f.geography_key, f.main_activity_key, f.juridical_form_key, g.region, a.label_fr") == (
        0, 0, 0, "Unknown", "Inconnu",
    )


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_every_dimension_has_an_unknown_member_with_key_0(warehouse, db, dimension):
    key = dimension.removeprefix("dim_") + "_key"

    assert db.column(f"SELECT count(*) FROM dwh.{dimension} WHERE {key} = 0") == [1]


def test_facts_cannot_reference_a_missing_dimension_member(warehouse, db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("UPDATE dwh.fact_enterprise SET geography_key = 999999")


# --- refreshing ---------------------------------------------------------------------------


def test_refresh_follows_the_source_and_keeps_surrogate_keys(warehouse, db):
    keys_before = dict(db.query("SELECT enterprise_number, enterprise_key FROM dwh.dim_enterprise"))
    write_update(
        warehouse,
        deletes={"denomination": [COMPANY]},
        inserts={
            "enterprise": [{"EnterpriseNumber": NEWCOMER, "TypeOfEnterprise": "1", "StartDate": "01-01-2026"}],
            "denomination": [{"EntityNumber": COMPANY, "Language": "2", "TypeOfDenomination": "001", "Denomination": "Alpha Groep NV"}],
        },
    )
    load_kbo_to_postgres(NEXT_DATE, archive_type="Update", data_dir=warehouse)

    refresh(db)

    keys_after = dict(db.query("SELECT enterprise_number, enterprise_key FROM dwh.dim_enterprise"))
    assert {number: keys_after[number] for number in keys_before} == keys_before  # keys are stable
    assert NEWCOMER in keys_after
    assert db.column("SELECT count(*) FROM dwh.fact_enterprise") == [3]
    # type 1 change: the new name replaces the old one, no history row is added
    assert db.column(f"SELECT name FROM dwh.dim_enterprise WHERE enterprise_number = '{COMPANY}'") == ["Alpha Groep NV"]


def test_refreshing_twice_gives_the_same_warehouse(warehouse, db):
    first = refresh(db)

    assert refresh(db) == first


def test_every_refresh_is_recorded_with_its_source_extract(warehouse, db):
    refresh(db)

    batches = db.query("SELECT source_extract_number, row_counts->>'fact_enterprise' FROM dwh.etl_batch ORDER BY batch_id")
    assert batches == [(FULL_NUMBER, "2"), (FULL_NUMBER, "2")]


def test_refresh_needs_loaded_data(db):
    with pytest.raises(RuntimeError, match="load a Full archive"):
        refresh(db)
