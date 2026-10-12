import pandas as pd
import pytest

from collector.schemas import (
    PARKING_DAILY, PARKING_HOURLY, PARKING_STATION, PHPDT_DAILY,
    RIDERSHIP_DAILY, RIDERSHIP_HOURLY, RIDERSHIP_STATION,
)
from collector.upsert import upsert_rows


def test_insert_rows_into_empty_dataset():
    incoming = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Total": 100, "NCMC": 50},
        ]
    )

    result = upsert_rows(pd.DataFrame(), incoming, RIDERSHIP_DAILY)

    assert len(result.dataframe) == 1
    assert result.inserted == 1
    assert result.dataframe.iloc[0]["Total"] == 100


def test_replaying_same_payload_is_noop():
    existing = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Total": 100, "NCMC": 50},
        ]
    )
    incoming = existing.copy()

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)

    assert result.dataframe.to_dict(orient="records") == existing.to_dict(orient="records")
    assert result.decisions[0].action == "noop"


def test_existing_row_updates_when_incoming_numbers_increase():
    existing = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Total": 100, "NCMC": 0},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Total": 180, "NCMC": 80},
        ]
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)

    row = result.dataframe.iloc[0]
    assert row["Total"] == 180
    assert row["NCMC"] == 80
    assert result.decisions[0].action == "updated"
    assert result.decisions[0].updated_columns == ["Total", "NCMC"]


def test_lower_incoming_values_are_anomalies_and_do_not_overwrite():
    existing = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Total": 180, "NCMC": 80},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Total": 100, "NCMC": 0},
        ]
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)

    row = result.dataframe.iloc[0]
    assert row["Total"] == 180
    assert row["NCMC"] == 80
    assert result.decisions[0].action == "anomaly"
    assert [conflict.column for conflict in result.decisions[0].conflicts] == ["Total", "NCMC"]


def test_hourly_rows_update_by_date_and_hour_key():
    existing = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Hour": "10:00", "Total": 100},
            {"Date": "2026-04-30", "Hour": "11:00", "Total": 150},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Hour": "10:00", "Total": 125},
        ]
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_HOURLY)

    assert result.dataframe.loc[result.dataframe["Hour"] == "10:00", "Total"].iloc[0] == 125
    assert result.dataframe.loc[result.dataframe["Hour"] == "11:00", "Total"].iloc[0] == 150


def test_duplicate_incoming_keys_are_rejected():
    incoming = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Total": 100},
            {"Date": "2026-04-30", "Total": 120},
        ]
    )

    with pytest.raises(ValueError, match="duplicate natural keys"):
        upsert_rows(pd.DataFrame(), incoming, RIDERSHIP_DAILY)


def test_column_aliases_are_normalized_before_upsert():
    existing = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Line": "01", "Station": "SWD", "Eight Wheeler": ""},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"Date": "2026-04-30", "Line": "01", "Station": "SWD", "Eight Wheleer": 2},
        ]
    )

    result = upsert_rows(existing, incoming, PARKING_STATION)

    assert "Eight Wheleer" not in result.dataframe.columns
    assert result.dataframe.iloc[0]["Eight Wheeler"] == 2

@pytest.mark.parametrize("schema", [RIDERSHIP_DAILY, RIDERSHIP_HOURLY, RIDERSHIP_STATION,
                                   PARKING_DAILY, PARKING_HOURLY, PARKING_STATION, PHPDT_DAILY])
def test_key_lookup_matches_original_with_existing_row_labels(schema):
    keys = {"Date": "2026-10-11", "Hour": "10:00", "Line": "01", "Station": "SWD",
            "Direction": "UP", "Start Station": "SAP", "End Station": "SME",
            "Start Hour": "10:00", "End Hour": "11:00"}
    first = {key: keys[key] for key in schema.key_columns}
    second = {**first, "Date": "2026-10-12"}
    existing = pd.DataFrame([{**first, "Count": 10}, {**second, "Count": 20}], index=[1, 2])
    old = {tuple(row[key] for key in schema.key_columns): index for index, row in existing.iterrows()}
    new = dict(zip(existing[list(schema.key_columns)].itertuples(index=False, name=None), existing.index))
    assert new == old
    incoming = pd.DataFrame([{**first, "Count": 11}, {**first, "Date": "2026-10-13", "Count": 30}])
    result = upsert_rows(existing, incoming, schema)
    assert list(result.dataframe["Count"]) == [11, 20, 30]
    if "Line" in schema.key_columns:
        assert list(result.dataframe["Line"]) == ["01", "01", "01"]
