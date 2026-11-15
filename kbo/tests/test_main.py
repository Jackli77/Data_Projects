"""Tests for main.py — pure functions only."""

import argparse
from datetime import date

import pytest

from main import date_range, parse_date


def test_parse_date_valid():
    assert parse_date("2026-11-14") == date(2026, 11, 14)
    assert parse_date("2025-01-01") == date(2025, 1, 1)


def test_parse_date_invalid():
    for bad in ("not-a-date", "14-11-2026", "2026-13-01"):
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid date"):
            parse_date(bad)


def test_date_range_normal():
    result = date_range(date(2026, 11, 11), date(2026, 11, 14))
    assert result == [
        date(2026, 11, 11),
        date(2026, 11, 12),
        date(2026, 11, 13),
        date(2026, 11, 14),
    ]


def test_date_range_single_day():
    result = date_range(date(2026, 11, 14), date(2026, 11, 14))
    assert result == [date(2026, 11, 14)]


def test_date_range_end_before_start():
    with pytest.raises(ValueError, match="cannot be earlier"):
        date_range(date(2026, 11, 14), date(2026, 11, 11))