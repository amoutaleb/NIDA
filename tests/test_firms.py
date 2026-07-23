"""
NiDa — Tests for the FIRMS client CSV parsing, quality filtering,
and multi-satellite deduplication.
Run with:  pytest tests/ -v
"""

import pandas as pd
import pytest

from backend.data_layer.firms_client import _deduplicate, _filter_quality, _parse_csv, FIRMSError

SAMPLE_CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
36.75,4.05,340.5,0.39,0.36,2026-07-14,0130,N,VIIRS,n,2.0NRT,295.2,12.4,N
36.44,5.07,367.1,0.41,0.37,2026-07-14,0130,N20,VIIRS,h,2.0NRT,301.8,45.7,N
35.20,1.30,301.2,0.44,0.39,2026-07-14,0130,N,VIIRS,l,2.0NRT,289.0,2.1,N
"""


def test_parse_valid_csv():
    df = _parse_csv(SAMPLE_CSV)
    # low-confidence row should be dropped
    assert len(df) == 2
    assert set(df["confidence"]) == {"n", "h"}
    # brightness column unified from bright_ti4
    assert "brightness" in df.columns


def test_parse_empty_response():
    df = _parse_csv("")
    assert df.empty


def test_parse_header_only():
    header = SAMPLE_CSV.splitlines()[0]
    df = _parse_csv(header)
    assert df.empty


def test_invalid_key_response():
    with pytest.raises(FIRMSError):
        _parse_csv("Invalid MAP_KEY.")


def test_quality_filter_numeric_confidence():
    df = pd.DataFrame({
        "latitude": [36.0, 36.1],
        "longitude": [4.0, 4.1],
        "acq_date": ["2026-07-14"] * 2,
        "confidence": [80, 10],
    })
    out = _filter_quality(df)
    assert len(out) == 1
    assert out.iloc[0]["confidence"] == 80


def test_deduplicate_cross_satellite():
    """Same physical fire detected by two different VIIRS satellites
    (e.g. N and N20) at nearly identical coordinates should collapse
    to one record, not be double-counted or double-alerted."""
    df = pd.DataFrame({
        "latitude": [36.4820, 36.48205, 30.0000],
        "longitude": [3.3530, 3.35299, 5.0000],
        "acq_date": ["2026-07-13", "2026-07-13", "2026-07-13"],
        "acq_time": ["0056", "0056", "0056"],
        "satellite": ["N20", "N", "N20"],
    })
    out = _deduplicate(df)
    # first two rows are the same fire seen by two satellites -> collapse to 1
    # third row is a genuinely different location -> kept
    assert len(out) == 2


def test_deduplicate_keeps_distinct_fires():
    df = pd.DataFrame({
        "latitude": [36.0, 30.0, 28.5],
        "longitude": [4.0, 5.0, 7.5],
        "acq_date": ["2026-07-14"] * 3,
        "acq_time": ["0100"] * 3,
        "satellite": ["N20", "N20", "N20"],
    })
    out = _deduplicate(df)
    assert len(out) == 3
