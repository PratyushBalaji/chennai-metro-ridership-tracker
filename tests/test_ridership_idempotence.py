import copy
from datetime import datetime
import json
from pathlib import Path
import shutil

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest


pytestmark = pytest.mark.idempotence
FIXTURES = Path(__file__).parent / "fixtures/idempotence"
DATE = "2026-06-15"
FILES = {
    "daily": "Ridership/ChennaiMetro_Daily_Ridership.csv",
    "hourly": "Ridership/ChennaiMetro_Hourly_Ridership.csv",
    "station": "Ridership/ChennaiMetro_Station_Ridership.csv",
}
KEYS = {"daily": ["Date"], "hourly": ["Date", "Hour"], "station": ["Date", "Line", "Station"]}
CARDS = {"daily": "noOfNCMCcard", "hourly": "Singara Chennai Card", "station": "Singara Chennai Card"}
QR = {"daily": "noOfPaperQR", "hourly": "Paper QR", "station": "Paper QR"}


def read_outputs(output):
    return {name: pd.read_csv(output / filename, dtype=str, keep_default_na=False)
            for name, filename in FILES.items()}


def target(frame, name, date=DATE):
    mask = frame["Date"] == date
    if name == "hourly":
        mask &= frame["Hour"] == "10:00"
    elif name == "station":
        mask &= (frame["Line"] == "01") & (frame["Station"] == "SCC")
    return mask


def assert_outputs(output, expected):
    for name, actual in read_outputs(output).items():
        assert not actual.duplicated(KEYS[name]).any()
        assert_frame_equal(actual.sort_values(KEYS[name]).reset_index(drop=True),
                           expected[name].sort_values(KEYS[name]).reset_index(drop=True))


@pytest.fixture
def cases():
    return json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.fixture
def scenario(tmp_path, cases, run_collector, http_mock):
    output = tmp_path / "output"
    shutil.copytree(FIXTURES / "seed", output)

    def run(case_name, day="1", exit_code=0, date=None, payload=None):
        payload = copy.deepcopy(cases[case_name] if payload is None else payload)
        if date is not None:
            payload["hourly"]["categories"] = [f"{date}T10:00:00"]
        base = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
        responses = {f"{base}{endpoint}/{day}": payload[name] for endpoint, name in (
            ("allTicketCount", "daily"), ("hourlybaseddata", "hourly"), ("stationData", "station"),
        )}
        response_file = tmp_path / "response.json"
        response_file.write_text(json.dumps({"responses": responses}), encoding="utf-8")
        args = ("--day", day, "--api-response", response_file, "--output-dir", output, "--strict")
        if exit_code:
            with pytest.raises(SystemExit) as error:
                run_collector("ridership", *args)
            assert error.value.code == exit_code
        else:
            run_collector("ridership", *args)
        assert not http_mock.calls

    return output, run


@pytest.mark.parametrize("day", ("0", "1"))
@pytest.mark.parametrize("case,values,conflicts,reason", (
    pytest.param("equal", (150, 100, 50), (), None, id="all-equal-noop"),
    pytest.param("all_higher", (180, 120, 60), (), None, id="all-higher-update"),
    pytest.param("higher_and_equal", (180, 100, 50), (), None, id="higher-and-equal-update"),
    pytest.param("all_lower", (150, 100, 50), ("Total", "card", "qr"), "incoming_value_decreased", id="all-lower-rejected"),
    pytest.param("lower_and_equal", (150, 100, 50), ("Total",), "incoming_value_decreased", id="lower-and-equal-rejected"),
    pytest.param("mixed", (150, 100, 50), ("card",), "incoming_value_decreased", id="mixed-whole-row-rejected"),
    pytest.param("missing_card", (150, 100, 50), ("card",), "incoming_value_missing", id="missing-value-rejected"),
))
def test_cli_strict_upsert_preserves_unrelated_rows_and_replays(day, case, values, conflicts, reason, scenario):
    output, run = scenario
    expected = read_outputs(output)
    for name, frame in expected.items():
        frame.loc[target(frame, name), ["Total", CARDS[name], QR[name]]] = list(map(str, values))
    log = output / "Discrepancies/anomaly_log.jsonl"
    prefix = b""

    for attempt in range(2):
        run(case, day, exit_code=2 if conflicts else 0)
        assert_outputs(output, expected)
        if not conflicts:
            assert not log.exists()
            continue

        current = log.read_bytes()
        assert current.startswith(prefix)
        prefix = current
        records = [json.loads(line) for line in current.splitlines()]
        assert len(records) == attempt + 1
        record = records[-1]
        assert record["source"] == "ridership"
        assert record["day"] == day
        assert record["date"] == DATE
        assert record["status"] == ("pending_retry" if day == "0" else "needs_review")
        assert record["reason"] == "strict_upsert_anomaly"
        assert datetime.fromisoformat(record["utc_timestamp"].replace("Z", "+00:00")) == datetime.fromisoformat(record["ist_timestamp"])
        assert record["ist_timestamp"].endswith("+05:30")
        assert record["payload_summary"]["hourly_points"] == 1
        assert record["payload_summary"]["station_count"] == 1
        anomalies = {entry["dataset"]: entry for entry in record["anomalies"]}
        assert set(anomalies) == {f"ridership_{name}" for name in FILES}
        for name in FILES:
            entry = anomalies[f"ridership_{name}"]
            columns = {"Total": "Total", "card": CARDS[name], "qr": QR[name]}
            assert {detail["column"] for detail in entry["details"]} == {columns[c] for c in conflicts}
            assert {detail["reason"] for detail in entry["details"]} == {reason}
            assert entry["action"] == "anomaly"
            assert entry["key"] == {column: expected[name].loc[target(expected[name], name), column].item()
                                    for column in KEYS[name]}


@pytest.mark.parametrize("date", ("1999-12-31", "2026-06-14", "2100-01-01"))
def test_insert_before_between_or_after_existing_dates_is_idempotent(date, scenario):
    output, run = scenario
    expected = read_outputs(output)
    for name, frame in expected.items():
        new_row = frame.loc[target(frame, name)].copy()
        new_row["Date"] = date
        expected[name] = pd.concat([frame, new_row], ignore_index=True)
    for _ in range(2):
        run("equal", day="0", date=date)
        assert_outputs(output, expected)
    assert not (output / "Discrepancies/anomaly_log.jsonl").exists()


def test_blank_existing_values_can_be_backfilled(scenario):
    output, run = scenario
    expected = read_outputs(output)
    for name, frame in expected.items():
        incomplete = frame.copy()
        incomplete.loc[target(incomplete, name), CARDS[name]] = ""
        incomplete.to_csv(output / FILES[name], index=False)
    for _ in range(2):
        run("equal")
        assert_outputs(output, expected)
    assert not (output / "Discrepancies/anomaly_log.jsonl").exists()


def test_live_growth_final_replay_and_next_day_are_independent(scenario):
    output, run = scenario
    expected = read_outputs(output)
    run("equal", day="0")
    assert_outputs(output, expected)
    for name, frame in expected.items():
        frame.loc[target(frame, name), ["Total", CARDS[name], QR[name]]] = ["180", "120", "60"]
    run("all_higher", day="0")
    assert_outputs(output, expected)
    run("all_higher", day="1")
    assert_outputs(output, expected)
    for name, frame in expected.items():
        next_day = frame.loc[target(frame, name)].copy()
        next_day["Date"] = "2026-06-16"
        next_day[["Total", CARDS[name], QR[name]]] = ["30", "20", "10"]
        expected[name] = pd.concat([frame, next_day], ignore_index=True)
    run("next_day", day="0")
    assert_outputs(output, expected)
    run("all_higher", day="1")
    assert_outputs(output, expected)
    assert not (output / "Discrepancies/anomaly_log.jsonl").exists()


def test_successful_retry_preserves_the_anomaly_log(scenario):
    output, run = scenario
    run("mixed", day="0", exit_code=2)
    log = output / "Discrepancies/anomaly_log.jsonl"
    original_log = log.read_bytes()
    expected = read_outputs(output)
    for name, frame in expected.items():
        frame.loc[target(frame, name), ["Total", CARDS[name], QR[name]]] = ["180", "120", "60"]
    run("all_higher", day="0")
    assert_outputs(output, expected)
    assert log.read_bytes() == original_log


def test_bad_row_does_not_block_other_updates_or_new_keys(scenario, cases):
    output, run = scenario
    payload = copy.deepcopy(cases["mixed"])
    payload["hourly"]["categories"] += [f"{DATE}T11:00:00", f"{DATE}T12:00:00"]
    for series in payload["hourly"]["series"]:
        series["data"] += {"Total": [100, 40], "Paper QR": [30, 15], "Singara Chennai Card": [70, 25]}[series["name"]]
    payload["station"][0]["categories"].append("GUINDY")
    for series in payload["station"][0]["series"]:
        series["data"].append({"Total": 100, "Paper QR": 30, "Singara Chennai Card": 70}[series["name"]])
    payload["station"].append({"line": "02", "categories": ["CENTRAL  METRO", "ALANDUR"], "series": [
        {"name": "Total", "data": [90, 25]},
        {"name": "Paper QR", "data": [30, 10]},
        {"name": "Singara Chennai Card", "data": [60, 15]},
    ]})
    expected = read_outputs(output)
    hourly = expected["hourly"]
    hourly.loc[(hourly["Date"] == DATE) & (hourly["Hour"] == "11:00"),
               ["Total", "Paper QR", "Singara Chennai Card"]] = ["100", "30", "70"]
    expected["hourly"] = pd.concat([hourly, pd.DataFrame([
        {"Date": DATE, "Hour": "12:00", "Total": "40", "Paper QR": "15", "Singara Chennai Card": "25"},
    ])], ignore_index=True)
    station = expected["station"]
    for line, code, values in (("01", "SGU", ["100", "30", "70"]), ("02", "SCC", ["90", "30", "60"])):
        mask = (station["Date"] == DATE) & (station["Line"] == line) & (station["Station"] == code)
        station.loc[mask, ["Total", "Paper QR", "Singara Chennai Card"]] = values
    expected["station"] = pd.concat([station, pd.DataFrame([
        {"Date": DATE, "Line": "02", "Station": "SAL", "Total": "25", "Paper QR": "10", "Singara Chennai Card": "15"},
    ])], ignore_index=True)
    for _ in range(2):
        run("mixed", day="0", exit_code=2, payload=payload)
        assert_outputs(output, expected)
    records = (output / "Discrepancies/anomaly_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert all(len(json.loads(record)["anomalies"]) == 3 for record in records)
