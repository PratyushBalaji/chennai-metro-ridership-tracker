import pandas as pd

from collector.schemas import RIDERSHIP_DAILY
from collector.upsert import upsert_rows


def _frame(*rows):
    return pd.DataFrame(list(rows))


def _only_decision(result):
    assert len(result.decisions) == 1
    return result.decisions[0]


def test_equal_static_replay_is_noop():
    existing = _frame(
        {"Date": "2026-04-30", "Total": 180, "NCMC": 80, "Paper QR": 60},
    )
    incoming = _frame(
        {"Date": "2026-04-30", "Total": "180", "NCMC": 80.0, "Paper QR": 60},
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)
    decision = _only_decision(result)

    assert result.dataframe.to_dict(orient="records") == existing.to_dict(orient="records")
    assert decision.action == "noop"
    assert decision.updated_columns == []
    assert decision.conflicts == []


def test_equal_or_larger_live_values_update_the_row():
    existing = _frame(
        {"Date": "2026-04-30", "Total": 100, "NCMC": 40, "Paper QR": 25},
    )
    incoming = _frame(
        {"Date": "2026-04-30", "Total": 150, "NCMC": 40, "Paper QR": 30},
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)
    decision = _only_decision(result)
    row = result.dataframe.iloc[0]

    assert row["Total"] == 150
    assert row["NCMC"] == 40
    assert row["Paper QR"] == 30
    assert decision.action == "updated"
    assert decision.updated_columns == ["Total", "Paper QR"]
    assert decision.conflicts == []


def test_equal_or_smaller_incoming_values_are_anomalous():
    existing = _frame(
        {"Date": "2026-04-30", "Total": 150, "NCMC": 40, "Paper QR": 30},
    )
    incoming = _frame(
        {"Date": "2026-04-30", "Total": 100, "NCMC": 40, "Paper QR": 20},
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)
    decision = _only_decision(result)
    row = result.dataframe.iloc[0]

    assert row["Total"] == 150
    assert row["NCMC"] == 40
    assert row["Paper QR"] == 30
    assert decision.action == "anomaly"
    assert decision.updated_columns == []
    assert [conflict.column for conflict in decision.conflicts] == ["Total", "Paper QR"]
    assert {conflict.reason for conflict in decision.conflicts} == {"incoming_value_decreased"}


def test_mixed_row_is_anomalous_and_does_not_partially_update():
    existing = _frame(
        {"Date": "2026-04-30", "Total": 100, "NCMC": 50, "Paper QR": 25},
    )
    incoming = _frame(
        {"Date": "2026-04-30", "Total": 125, "NCMC": 40, "Paper QR": 25},
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)
    decision = _only_decision(result)
    row = result.dataframe.iloc[0]

    assert row["Total"] == 100
    assert row["NCMC"] == 50
    assert row["Paper QR"] == 25
    assert decision.action == "anomaly"
    assert decision.updated_columns == []
    assert [(conflict.column, conflict.existing, conflict.incoming, conflict.reason) for conflict in decision.conflicts] == [
        ("NCMC", 50, 40, "incoming_value_decreased"),
    ]
    assert result.updated == 0
    assert result.anomalies == 1
    assert result.conflicts == 1


def test_partial_existing_row_can_be_backfilled_without_conflict():
    existing = _frame(
        {"Date": "2026-04-30", "Total": 100, "NCMC": "", "Paper QR": 25},
    )
    incoming = _frame(
        {"Date": "2026-04-30", "Total": 100, "NCMC": 30, "Paper QR": 25},
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)
    decision = _only_decision(result)
    row = result.dataframe.iloc[0]

    assert row["Total"] == 100
    assert row["NCMC"] == 30
    assert row["Paper QR"] == 25
    assert decision.action == "updated"
    assert decision.updated_columns == ["NCMC"]
    assert decision.conflicts == []


def test_new_live_date_is_inserted_without_touching_existing_static_date():
    existing = _frame(
        {"Date": "2026-04-30", "Total": 150, "NCMC": 40},
    )
    incoming = _frame(
        {"Date": "2026-05-01", "Total": 25, "NCMC": 10},
    )

    result = upsert_rows(existing, incoming, RIDERSHIP_DAILY)
    decision = _only_decision(result)

    assert result.dataframe.to_dict(orient="records") == [
        {"Date": "2026-04-30", "Total": 150, "NCMC": 40},
        {"Date": "2026-05-01", "Total": 25, "NCMC": 10},
    ]
    assert decision.action == "inserted"
