import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from collector import ridership
import collector.anomalies as anomaly_helpers


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def retry_files(tmp_path):
    output = tmp_path / "output"
    shutil.copytree(FIXTURES / "idempotence/seed/Ridership", output / "ridership")
    cases = json.loads((FIXTURES / "idempotence/cases.json").read_text())["cases"]
    previous = {
        "source": "ridership", "day": "0", "date": "2026-06-15", "status": "pending_retry",
        "utc_timestamp": "2026-06-15T11:40:00Z", "ist_timestamp": "2026-06-15T17:10:00+05:30",
        "reason": "strict_upsert_anomaly", "anomalies": [], "payload_summary": {},
    }
    log = output / "discrepancies/anomaly_log.jsonl"
    anomaly_helpers.append_jsonl(log, previous)
    return output, cases, log, previous


def response_file(tmp_path, payload, day):
    base = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
    bundle = {"responses": {base + endpoint + "/" + day: payload[name] for endpoint, name in (
        ("allTicketCount", "daily"), ("hourlybaseddata", "hourly"), ("stationData", "station"),
    )}}
    path = tmp_path / "response.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


@pytest.mark.parametrize("day", ("0", "1"))
@pytest.mark.parametrize("hourly", ({"categories": [], "series": []}, {}, None))
def test_unavailable_hourly_data_logs_and_preserves_csvs(day, hourly, tmp_path, run_collector, http_mock):
    output = tmp_path / "output"
    shutil.copytree(FIXTURES / "idempotence/seed/Ridership", output / "ridership")
    before = {path: path.read_bytes() for path in output.rglob("*.csv")}
    payload = json.loads((FIXTURES / "idempotence/cases.json").read_text())["cases"]["empty_hourly"]
    payload["hourly"] = hourly
    fixture = response_file(tmp_path, payload, day)
    with pytest.raises(SystemExit) as error:
        run_collector("ridership", "--day", day, "--api-response", fixture, "--output-dir", output)
    assert error.value.code == 2
    assert {path: path.read_bytes() for path in before} == before
    record = anomaly_helpers.read_last_record(output / "discrepancies/anomaly_log.jsonl")
    assert record["status"] == ("pending_retry" if day == "0" else "needs_review")
    assert record["reason"] == "payload_missing_hourly_categories"
    expected = datetime.fromisoformat(record["ist_timestamp"]).date() - timedelta(days=int(day))
    assert record["date"] == expected.isoformat()
    assert record["payload_summary"]["date_inferred"] is True
    assert not http_mock.calls


def test_unavailable_payload_dry_run_writes_nothing(tmp_path, run_collector):
    output = tmp_path / "output"
    payload = json.loads((FIXTURES / "idempotence/cases.json").read_text())["cases"]["empty_hourly"]
    fixture = response_file(tmp_path, payload, "0")
    with pytest.raises(SystemExit) as error:
        run_collector("ridership", "--day", "0", "--api-response", fixture, "--output-dir", output, "--dry-run")
    assert error.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("case,status,code", (("all_higher", "resolved", 0), ("mixed", "pending_retry", 2),
                                             ("next_day", "pending_retry", 2), ("empty_hourly", "pending_retry", 2)))
def test_retry_collects_and_appends_status_only_for_its_date(case, status, code, retry_files, tmp_path, run_collector, http_mock):
    output, cases, log, previous = retry_files
    fixture = response_file(tmp_path, cases[case], "0")
    original_log = log.read_bytes()
    csvs = {path: pd.read_csv(path, dtype=str, keep_default_na=False) for path in output.rglob("*.csv")}
    original_csvs = {path: path.read_bytes() for path in csvs}
    with patch.object(anomaly_helpers, "datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
        args = ("--retry", "--api-response", fixture, "--output-dir", output)
        if code:
            with pytest.raises(SystemExit) as error:
                run_collector("ridership", *args)
            assert error.value.code == code
        else:
            run_collector("ridership", *args)
    assert log.read_bytes().startswith(original_log)
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) == 2
    assert records[-1]["status"] == status
    assert records[-1]["date"] == previous["date"]
    if case == "all_higher":
        assert records[-1]["previous"]["utc_timestamp"] == previous["utc_timestamp"]
    if case == "next_day":
        assert records[-1]["reason"] == "payload_date_mismatch"
        assert records[-1]["payload_summary"]["returned_dataset_date"] == "2026-06-16"
    for path, expected in csvs.items():
        if case == "all_higher":
            mask = expected["Date"] == "2026-06-15"
            if "Hour" in expected:
                mask &= expected["Hour"] == "10:00"
            if "Station" in expected:
                mask &= (expected["Line"] == "01") & (expected["Station"] == "SCC")
            columns = ["Total", "noOfNCMCcard", "noOfPaperQR"] if "noOfNCMCcard" in expected else ["Total", "Singara Chennai Card", "Paper QR"]
            expected.loc[mask, columns] = ["180", "120", "60"]
        assert_frame_equal(pd.read_csv(path, dtype=str, keep_default_na=False), expected)
    if case in ("next_day", "empty_hourly"):
        assert {path: path.read_bytes() for path in original_csvs} == original_csvs
    assert not http_mock.calls


@pytest.mark.parametrize("now", ("2026-06-15T18:20:00+00:00", "2026-06-16T04:00:00+00:00"))
def test_expired_retry_appends_status_without_fetching(now, retry_files, tmp_path, run_collector):
    output, _, log, _ = retry_files
    before = {path: path.read_bytes() for path in output.rglob("*.csv")}
    old_log = log.read_bytes()
    with patch.object(anomaly_helpers, "datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime.fromisoformat(now)
        with pytest.raises(SystemExit) as error:
            run_collector("ridership", "--retry", "--output-dir", output, "--api-response", tmp_path / "unused.json")
    assert error.value.code == 2
    assert log.read_bytes().startswith(old_log)
    assert anomaly_helpers.read_last_record(log)["status"] == "expired"
    assert {path: path.read_bytes() for path in before} == before


def test_due_retry_dry_run_preserves_csvs_and_log(retry_files, tmp_path, run_collector):
    output, cases, log, _ = retry_files
    before = {path: path.read_bytes() for path in [*output.rglob("*.csv"), log]}
    fixture = response_file(tmp_path, cases["all_higher"], "0")
    with patch.object(anomaly_helpers, "datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
        run_collector("ridership", "--retry", "--dry-run", "--api-response", fixture, "--output-dir", output)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("source,day", (("parking", "1"), ("phpdt", "1"), ("ridership", "1")))
def test_other_log_records_do_not_hide_live_retry(source, day, retry_files, tmp_path, run_collector):
    output, cases, log, previous = retry_files
    anomaly_helpers.append_jsonl(log, {"source": source, "day": day, "status": "needs_review"})
    fixture = response_file(tmp_path, cases["all_higher"], "0")
    with patch.object(anomaly_helpers, "datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
        run_collector("ridership", "--retry", "--api-response", fixture, "--output-dir", output)
    record = anomaly_helpers.read_last_record(log)
    assert record["status"] == "resolved"
    assert record["date"] == previous["date"]
    assert len(log.read_text().splitlines()) == 3


@pytest.mark.parametrize("missing", ("empty_station", "other_station", "other_hour"))
@pytest.mark.parametrize("retry", (False, True))
def test_omitted_flagged_keys_stay_pending_until_returned(missing, retry, retry_files, tmp_path, run_collector):
    output, cases, log, _ = retry_files
    with patch.object(anomaly_helpers, "datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 6, 15, 11, 40, tzinfo=timezone.utc)
        with pytest.raises(SystemExit) as error:
            run_collector("ridership", "--day", "0", "--api-response",
                          response_file(tmp_path, cases["mixed"], "0"), "--output-dir", output)
        assert error.value.code == 2
        original_log = log.read_bytes()
        payload = copy.deepcopy(cases["all_higher"])
        if missing == "empty_station":
            payload["station"] = []
        elif missing == "other_station":
            payload["station"][0]["categories"] = ["GUINDY"]
        else:
            payload["hourly"]["categories"] = ["2026-06-15T11:00:00"]
        mode = ("--retry",) if retry else ("--day", "0")
        for minute in (0, 20):
            clock.now.return_value = datetime(2026, 6, 15, 12, minute, tzinfo=timezone.utc)
            with pytest.raises(SystemExit) as error:
                run_collector("ridership", *mode, "--api-response",
                              response_file(tmp_path, payload, "0"), "--output-dir", output)
            assert error.value.code == 2
            record = anomaly_helpers.read_last_record(log)
            assert record["status"] == "pending_retry"
            assert len(record["anomalies"]) == 1
            anomaly = record["anomalies"][0]
            assert anomaly["details"][0]["reason"] == "incoming_row_missing"
            if missing == "other_hour":
                assert anomaly["key"] == {"Date": "2026-06-15", "Hour": "10:00"}
            else:
                assert anomaly["key"] == {"Date": "2026-06-15", "Line": "01", "Station": "SCC"}
            assert pd.read_csv(output / ridership.DAILY_FILENAME).iloc[1]["Total"] == 180
        clock.now.return_value = datetime(2026, 6, 15, 12, 40, tzinfo=timezone.utc)
        run_collector("ridership", "--retry", "--api-response",
                      response_file(tmp_path, cases["all_higher"], "0"), "--output-dir", output)
    assert anomaly_helpers.read_last_record(log)["status"] == "resolved"
    assert log.read_bytes().startswith(original_log)


@pytest.mark.parametrize("interruption", ("empty_hourly", "next_day", "mixed"))
def test_retry_carries_omitted_keys_through_other_anomalies(interruption, retry_files, tmp_path, run_collector):
    output, cases, log, _ = retry_files
    flagged = {"dataset": "ridership_station", "key": {"Date": "2026-06-15", "Line": "01", "Station": "SCC"},
               "action": "anomaly", "details": []}
    previous = anomaly_helpers.read_last_record(log)
    previous["anomalies"] = [flagged]
    log.write_text(json.dumps(previous) + "\n")
    payload = copy.deepcopy(cases[interruption])
    payload["station"] = []
    with patch.object(anomaly_helpers, "datetime", wraps=datetime) as clock:
        for hour, minute, response in ((12, 0, payload), (12, 20, {**cases["all_higher"], "station": []})):
            clock.now.return_value = datetime(2026, 6, 15, hour, minute, tzinfo=timezone.utc)
            with pytest.raises(SystemExit) as error:
                run_collector("ridership", "--retry", "--api-response",
                              response_file(tmp_path, response, "0"), "--output-dir", output)
            assert error.value.code == 2
            record = anomaly_helpers.read_last_record(log)
            assert any(anomaly["key"] == flagged["key"] for anomaly in record["anomalies"])
            assert record["status"] == "pending_retry"


def test_invalid_station_keys_do_not_partially_write_ridership(tmp_path):
    _write_existing_ridership(tmp_path, total=100, card=50)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.csv")}
    daily, hourly, station = _payloads(total=150, card=100)
    station.append(copy.deepcopy(station[0]))
    with patch.object(ridership, "fetch_ridership_payloads", return_value=(daily, hourly, station)):
        with pytest.raises(ValueError, match="duplicate natural keys"):
            ridership.collect_ridership(output_dir=tmp_path)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("status", ("pending_retry", "resolved", "expired", "needs_review"))
def test_retry_skips_fetch_for_waiting_or_terminal_records(status, retry_files, tmp_path, run_collector):
    output, _, log, previous = retry_files
    previous.update(status=status, utc_timestamp="2026-06-15T11:50:00Z")
    log.write_text(json.dumps(previous) + "\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in [*output.rglob("*.csv"), log]}
    with patch.object(anomaly_helpers, "datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
        run_collector("ridership", "--retry", "--output-dir", output, "--api-response", tmp_path / "unused.json")
    assert {path: path.read_bytes() for path in before} == before


def test_unrelated_cli_error_still_exits_one(tmp_path):
    fixture = tmp_path / "bad.json"
    fixture.write_text('{"responses": {}}')
    result = subprocess.run([sys.executable, "-E", "-m", "collector.ridership", "--api-response", str(fixture),
                             "--output-dir", str(tmp_path / "output")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert not (tmp_path / "output").exists()


def test_retry_cli_starts_without_pythonpath_or_pending_log(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    script = Path(__file__).resolve().parents[1] / "ridership.py"
    result = subprocess.run(
        [sys.executable, "-E", "-m", "collector.ridership", "--retry", "--output-dir", str(tmp_path),
         "--api-response", str(tmp_path / "unused.json")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "retry: skip" in result.stdout
    assert not list(tmp_path.rglob("*.csv"))
    assert not list(tmp_path.rglob("*.jsonl"))


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
    daily_payload, hourly_payload, station_payload = _payloads(total=150, card=100)

    with patch.object(ridership, "fetch_ridership_payloads", return_value=(daily_payload, hourly_payload, station_payload)):
        ridership.collect_ridership(day="1", output_dir=tmp_path, dry_run=True)

    assert not (tmp_path / ridership.DAILY_FILENAME).exists()


def test_collect_ridership_upserts_all_three_outputs(tmp_path):
    daily_payload, hourly_payload, station_payload = _payloads(total=150, card=100)

    with patch.object(ridership, "fetch_ridership_payloads", return_value=(daily_payload, hourly_payload, station_payload)):
        ridership.collect_ridership(day="1", output_dir=tmp_path)

    daily = pd.read_csv(tmp_path / ridership.DAILY_FILENAME)
    hourly = pd.read_csv(tmp_path / ridership.HOURLY_FILENAME)
    station = pd.read_csv(tmp_path / ridership.STATION_FILENAME)

    assert daily.loc[0, "Total"] == 150
    assert hourly.loc[0, "Hour"] == "10:00"
    assert station.loc[0, "Station"] == "SCC"


def test_collect_ridership_logs_anomalies(tmp_path):
    _write_existing_ridership(tmp_path, total=200, card=120)
    daily_payload, hourly_payload, station_payload = _payloads(total=150, card=100)
    log_path = tmp_path / "discrepancies" / "anomaly_log.jsonl"

    with patch.object(ridership, "fetch_ridership_payloads", return_value=(daily_payload, hourly_payload, station_payload)):
        results = ridership.collect_ridership(day="0", output_dir=tmp_path, anomaly_log=log_path)

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert sum(result.anomalies for result in results) == 3
    assert record["source"] == "ridership"
    assert record["day"] == "0"
    assert record["date"] == "2026-04-30"
    assert record["status"] == "pending_retry"
    assert record["utc_timestamp"].endswith("Z")
    assert "+05:30" in record["ist_timestamp"]
    assert record["payload_summary"]["daily_total"] == 150
    assert record["payload_summary"]["station_count"] == 1
    assert any(
        detail["column"] == "Singara Chennai Card"
        for anomaly in record["anomalies"]
        for detail in anomaly["details"]
    )


def _payloads(total: int, card: int):
    daily_payload = {"totalTickets": total, "noOfNCMCcard": card}
    hourly_payload = {
        "categories": ["2026-04-30T10:00:00"],
        "series": [
            {"name": "Total", "data": [total]},
            {"name": "Singara Chennai Card", "data": [card]},
        ],
    }
    station_payload = [
        {
            "line": "01",
            "categories": ["CENTRAL  METRO"],
            "series": [
                {"name": "Total", "data": [total]},
                {"name": "Singara Chennai Card", "data": [card]},
            ],
        }
    ]
    return daily_payload, hourly_payload, station_payload


def _write_existing_ridership(tmp_path, total: int, card: int) -> None:
    ridership_dir = tmp_path / "ridership"
    ridership_dir.mkdir()
    pd.DataFrame([
        {"Date": "2026-04-30", "Total": total, "noOfNCMCcard": card},
    ]).to_csv(tmp_path / ridership.DAILY_FILENAME, index=False)
    pd.DataFrame([
        {"Date": "2026-04-30", "Hour": "10:00", "Total": total, "Singara Chennai Card": card},
    ]).to_csv(tmp_path / ridership.HOURLY_FILENAME, index=False)
    pd.DataFrame([
        {"Date": "2026-04-30", "Line": "01", "Station": "SCC", "Total": total, "Singara Chennai Card": card},
    ]).to_csv(tmp_path / ridership.STATION_FILENAME, index=False)
import copy
from datetime import datetime, timedelta, timezone
