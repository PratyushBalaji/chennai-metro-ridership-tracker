from unittest.mock import patch

import pandas as pd

import ridership


def test_normalize_daily_uses_sorted_payment_columns():
    payload = {
        "totalTickets": 150,
        "noOfPaperQR": 40,
        "noOfNCMCcard": 100,
        "noOfSVC": 10,
    }

    dataframe = ridership.normalize_daily(payload, "2026-04-30")

    assert dataframe.columns.tolist() == ["Date", "Total", "noOfNCMCcard", "noOfPaperQR", "noOfSVC"]
    assert dataframe.iloc[0].to_dict() == {
        "Date": "2026-04-30",
        "Total": 150,
        "noOfNCMCcard": 100,
        "noOfPaperQR": 40,
        "noOfSVC": 10,
    }


def test_normalize_hourly_extracts_date_and_hour_rows():
    payload = {
        "categories": ["2026-04-30T10:00:00", "2026-04-30T11:00:00"],
        "series": [
            {"name": "Total", "data": [100, 150]},
            {"name": "Singara Chennai Card", "data": [55, 75]},
            {"name": "Paper QR", "data": [45, 75]},
        ],
    }

    dataframe = ridership.normalize_hourly(payload)

    assert dataframe.to_dict(orient="records") == [
        {
            "Date": "2026-04-30",
            "Hour": "10:00",
            "Total": 100,
            "Paper QR": 45,
            "Singara Chennai Card": 55,
        },
        {
            "Date": "2026-04-30",
            "Hour": "11:00",
            "Total": 150,
            "Paper QR": 75,
            "Singara Chennai Card": 75,
        },
    ]


def test_normalize_station_maps_station_names_to_codes_and_unions_payment_columns():
    payload = [
        {
            "line": "01",
            "categories": ["CENTRAL  METRO"],
            "series": [
                {"name": "Total", "data": [100]},
                {"name": "Paper QR", "data": [45]},
            ],
        },
        {
            "line": "02",
            "categories": ["ALANDUR"],
            "series": [
                {"name": "Total", "data": [120]},
                {"name": "Singara Chennai Card", "data": [80]},
            ],
        },
    ]

    dataframe = ridership.normalize_station(payload, "2026-04-30")

    assert dataframe.columns.tolist() == [
        "Date",
        "Line",
        "Station",
        "Total",
        "Paper QR",
        "Singara Chennai Card",
    ]
    assert dataframe[["Date", "Line", "Station", "Total"]].to_dict(orient="records") == [
        {"Date": "2026-04-30", "Line": "01", "Station": "SCC", "Total": 100},
        {"Date": "2026-04-30", "Line": "02", "Station": "SAL", "Total": 120},
    ]


def test_collect_ridership_dry_run_does_not_write_csvs(tmp_path):
    daily_payload = {"totalTickets": 150, "noOfNCMCcard": 100}
    hourly_payload = {
        "categories": ["2026-04-30T10:00:00"],
        "series": [
            {"name": "Total", "data": [150]},
            {"name": "Singara Chennai Card", "data": [100]},
        ],
    }
    station_payload = [
        {
            "line": "01",
            "categories": ["CENTRAL  METRO"],
            "series": [
                {"name": "Total", "data": [150]},
                {"name": "Singara Chennai Card", "data": [100]},
            ],
        }
    ]

    with patch.object(ridership, "fetch_ridership_payloads", return_value=(daily_payload, hourly_payload, station_payload)):
        ridership.collect_ridership(day="1", output_dir=tmp_path, dry_run=True)

    assert not (tmp_path / ridership.DAILY_FILENAME).exists()


def test_collect_ridership_upserts_all_three_outputs(tmp_path):
    daily_payload = {"totalTickets": 150, "noOfNCMCcard": 100}
    hourly_payload = {
        "categories": ["2026-04-30T10:00:00"],
        "series": [
            {"name": "Total", "data": [150]},
            {"name": "Singara Chennai Card", "data": [100]},
        ],
    }
    station_payload = [
        {
            "line": "01",
            "categories": ["CENTRAL  METRO"],
            "series": [
                {"name": "Total", "data": [150]},
                {"name": "Singara Chennai Card", "data": [100]},
            ],
        }
    ]

    with patch.object(ridership, "fetch_ridership_payloads", return_value=(daily_payload, hourly_payload, station_payload)):
        ridership.collect_ridership(day="1", output_dir=tmp_path)

    daily = pd.read_csv(tmp_path / ridership.DAILY_FILENAME)
    hourly = pd.read_csv(tmp_path / ridership.HOURLY_FILENAME)
    station = pd.read_csv(tmp_path / ridership.STATION_FILENAME)

    assert daily.loc[0, "Total"] == 150
    assert hourly.loc[0, "Hour"] == "10:00"
    assert station.loc[0, "Station"] == "SCC"
