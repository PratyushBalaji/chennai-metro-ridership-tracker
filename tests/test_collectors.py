from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest


FIXTURES = Path(__file__).parent / "fixtures"
FILES = {
    "ridership": (
        "Ridership/ChennaiMetro_Daily_Ridership.csv",
        "Ridership/ChennaiMetro_Hourly_Ridership.csv",
        "Ridership/ChennaiMetro_Station_Ridership.csv",
    ),
    "parking": (
        "Parking/ChennaiMetro_Daily_Parking.csv",
        "Parking/ChennaiMetro_Hourly_Parking.csv",
        "Parking/ChennaiMetro_Station_Parking.csv",
    ),
    "phpdt": ("PHPDT/ChennaiMetro_Daily_PHPDT.csv",),
}


def read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def canonical(frame):
    keys = [column for column in frame.columns if column in (
        "Date", "Line", "Direction", "Hour", "Station", "Start Hour",
        "End Hour", "Start Station", "End Station",
    )]
    return frame.sort_values(keys).reset_index(drop=True)


@pytest.mark.parametrize("script", FILES)
def test_api_response_to_csv_matches_expected(script, tmp_path, http_mock, run_collector):
    run_collector(script)
    assert len(http_mock.calls) == {"ridership": 3, "parking": 4, "phpdt": 1}[script]
    assert {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.csv")} == set(FILES[script])
    for filename in FILES[script]:
        assert_frame_equal(canonical(read_csv(tmp_path / filename)),
                           canonical(read_csv(FIXTURES / "expected" / filename)))


@pytest.mark.parametrize("script", FILES)
def test_identical_replay_preserves_values_and_row_count(script, tmp_path, http_mock, run_collector):
    run_collector(script)
    before = {filename: read_csv(tmp_path / filename) for filename in FILES[script]}
    run_collector(script)
    for filename, expected in before.items():
        assert_frame_equal(read_csv(tmp_path / filename), expected)


@pytest.mark.parametrize("script", FILES)
@pytest.mark.parametrize("position", ("before_latest", "same_latest", "after_latest"))
def test_incoming_date_with_existing_csv(script, position, tmp_path, http_mock, run_collector):
    before = {}
    for filename in FILES[script]:
        incoming = read_csv(FIXTURES / "expected" / filename)
        seed = read_csv(FIXTURES / "seed" / filename)
        if position == "same_latest":
            seed = pd.concat([seed[seed["Date"] == "2000-01-01"], incoming], ignore_index=True)
        elif position == "after_latest":
            seed = seed[seed["Date"] == "2000-01-01"].reset_index(drop=True)
        seed.to_csv(tmp_path / filename, index=False)
        before[filename] = seed

    run_collector(script)
    for filename, seed in before.items():
        incoming = read_csv(FIXTURES / "expected" / filename)
        expected = seed if position == "same_latest" else pd.concat([seed, incoming], ignore_index=True)
        assert_frame_equal(canonical(read_csv(tmp_path / filename)), canonical(expected))


@pytest.mark.parametrize("script", FILES)
def test_empty_existing_csv_is_populated(script, tmp_path, http_mock, run_collector):
    for filename in FILES[script]:
        (tmp_path / filename).touch()
    run_collector(script)
    for filename in FILES[script]:
        assert_frame_equal(canonical(read_csv(tmp_path / filename)),
                           canonical(read_csv(FIXTURES / "expected" / filename)))


def test_parking_can_use_dataset_date_from_workflow(tmp_path, http_mock, run_collector, monkeypatch):
    monkeypatch.setenv("DATASET_DATE", "2026-10-11")
    run_collector("parking")
    assert len(http_mock.calls) == 3
    for filename in FILES["parking"]:
        assert_frame_equal(canonical(read_csv(tmp_path / filename)),
                           canonical(read_csv(FIXTURES / "expected" / filename)))
