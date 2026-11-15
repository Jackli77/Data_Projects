"""Tests for helper.py."""

import pandas as pd
import pytest

from helper import inspect_data


def test_inspect_data_empty():
    df = pd.DataFrame()
    # Should not raise on empty DataFrame
    inspect_data(df)


def test_inspect_data_non_empty(sample_df):
    # Should not raise; prints summary
    inspect_data(sample_df)
    assert isinstance(sample_df, pd.DataFrame)
    assert len(sample_df) == 2