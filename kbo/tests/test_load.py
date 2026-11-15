"""Tests for load.py — pure functions only (no DB)."""

import pytest

from load import _to_snake_case


def test_to_snake_case_pascal():
    assert _to_snake_case("EnterpriseNumber") == "enterprise_number"
    assert _to_snake_case("JuridicalFormCAC") == "juridical_form_cac"
    assert _to_snake_case("CountryNL") == "country_nl"
    assert _to_snake_case("DateStrikingOff") == "date_striking_off"


def test_to_snake_case_already_snake():
    assert _to_snake_case("enterprise_number") == "enterprise_number"
    assert _to_snake_case("start_date") == "start_date"


def test_to_snake_case_mixed():
    assert _to_snake_case("EntityNumber") == "entity_number"
    assert _to_snake_case("TypeOfAddress") == "type_of_address"
    assert _to_snake_case("ExtraAddressInfo") == "extra_address_info"
    assert _to_snake_case("NaceVersion") == "nace_version"


def test_to_snake_case_whitespace():
    assert _to_snake_case("  EnterpriseNumber  ") == "enterprise_number"