import copy
import json
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
import responses

from collector import ridership
from collector.anomalies import read_last_record
from collector.schemas import CORE_HOURS


FIXTURES = Path(__file__).parent / "fixtures"
BASE = ridership.BASE_URL


def live_bundle(bundle, recorded_at="2026-11-06T12:00:00+00:00"):
    bundle = copy.deepcopy(bundle)
    bundle["recorded_at_utc"] = recorded_at
    bundle["day"] = "0"
    bundle["responses"] = {url[:-1] + "0": payload for url, payload in bundle["responses"].items()}
    return bundle


def payload(bundle, endpoint):
    return next(value for key, value in bundle["responses"].items() if key.endswith(endpoint + "/" + bundle["day"]))


def drop_hour(bundle, hour):
    hourly = payload(bundle, "hourlybaseddata")
    index = next(i for i, value in enumerate(hourly["categories"]) if value[11:16] == hour)
    hourly["categories"].pop(index)
    for series in hourly["series"]:
        series["data"].pop(index)


def records(tmp_path):
    return [json.loads(line) for line in (tmp_path / "output/discrepancies/anomaly_log.jsonl").read_text().splitlines()]


def csvs(tmp_path):
    return {path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*.csv")}


def test_complete_snapshot_publishes_all_three(snapshot_bundle, collect_snapshot, tmp_path):
    result = collect_snapshot(snapshot_bundle)
    assert (result.exit_code, result.status, result.published) == (0, "published_final", True)
    assert len(csvs(tmp_path)) == 3
    frames = [pd.read_csv(tmp_path / "output" / name, dtype=str) for name in ridership.FILENAMES]
    assert [len(frame) for frame in frames] == [1, 21, 54]
    assert [pd.to_numeric(frame.Total).sum() for frame in frames] == [270, 270, 270]
    assert set(frames[2].Line) == {"01", "02", "04"}
    assert len(records(tmp_path)) == 1


@pytest.mark.parametrize("change,code", [
    ("hour", "missing_hours"), ("station", "station_coverage"), ("unknown_station", "station_coverage"),
    ("duplicate_station", "duplicate_station"), ("duplicate_hour", "duplicate_hour"),
    ("duplicate_series", "duplicate_series"), ("short_series", "series_length_mismatch"),
    ("long_series", "series_length_mismatch"), ("missing_fare", "missing_fare"),
    ("empty_hourly", "empty_hourly"), ("bad_timestamp", "invalid_timestamp"),
    ("wrong_date", "invalid_timestamp"), ("mixed_date", "invalid_timestamp"),
    ("metadata", "missing_or_invalid_capture_timestamp"), ("rollover", "capture_day_rollover"),
])
def test_structural_failures_never_publish(change, code, snapshot_bundle, collect_snapshot, tmp_path):
    daily = payload(snapshot_bundle, "allTicketCount")
    hourly = payload(snapshot_bundle, "hourlybaseddata")
    station = payload(snapshot_bundle, "stationData")
    if change == "hour":
        drop_hour(snapshot_bundle, "22:00")
    elif change == "station":
        station.pop()
    elif change == "unknown_station":
        station[0]["categories"][0] = "unmapped station"
    elif change == "duplicate_station":
        station.append(copy.deepcopy(station[0]))
    elif change == "duplicate_hour":
        hourly["categories"][1] = hourly["categories"][0]
    elif change == "duplicate_series":
        hourly["series"].append(copy.deepcopy(hourly["series"][0]))
    elif change == "short_series":
        hourly["series"][0]["data"].pop()
    elif change == "long_series":
        hourly["series"][0]["data"].append(0)
    elif change == "missing_fare":
        del daily["noOfTVM"]
    elif change == "empty_hourly":
        hourly["categories"] = []
        for series in hourly["series"]:
            series["data"] = []
    elif change == "bad_timestamp":
        hourly["categories"][0] = "invalid"
    elif change == "wrong_date":
        hourly["categories"] = [value.replace("2026-11-06", "2026-11-05") for value in hourly["categories"]]
    elif change == "mixed_date":
        hourly["categories"][0] = "2026-11-05T00:00:00"
    elif change == "metadata":
        del snapshot_bundle["recorded_at_utc"]
    elif change == "rollover":
        snapshot_bundle["recorded_at_utc"] = "2026-11-07T18:29:50+00:00"
        snapshot_bundle["finished_at_utc"] = "2026-11-07T18:30:05+00:00"
    result = collect_snapshot(snapshot_bundle)
    assert result.exit_code == 2
    assert any(item["code"] == code and item["kind"] == "structural" for item in result.findings)
    assert csvs(tmp_path) == {}
    assert result.status == "needs_review"
    assert len(result.raw_paths) == (1 if change == "rollover" else 2)


@pytest.mark.parametrize("value", [-1, 1.5, True, None, "", float("nan"), float("inf")])
def test_invalid_counts_are_not_coerced_to_zero(value, snapshot_bundle, collect_snapshot, tmp_path):
    payload(snapshot_bundle, "allTicketCount")["totalTickets"] = value
    result = collect_snapshot(snapshot_bundle)
    assert result.exit_code == 2
    assert any(f["code"] == "invalid_count" for f in result.findings)
    assert not csvs(tmp_path)


def test_numeric_strings_are_valid_counts(snapshot_bundle, collect_snapshot):
    for response in snapshot_bundle["responses"].values():
        if isinstance(response, dict) and "totalTickets" in response:
            response.update({key: str(value) for key, value in response.items()})
        else:
            for line in response if isinstance(response, list) else [response]:
                for series in line["series"]:
                    series["data"] = list(map(str, series["data"]))
    assert collect_snapshot(snapshot_bundle).exit_code == 0


def test_nonzero_qr_and_ondc_components_are_not_double_counted(snapshot_bundle, collect_snapshot):
    counts = {"Singara Chennai Card": 54, "Paper QR": 54, "RedBus QR (ONDC)": 54,
              "UTP QR": 54, "TVM": 54}
    payload(snapshot_bundle, "allTicketCount").update(
        noOfNCMCcard=54, noOfPaperQR=54, noOfRedBusQR=54, noOfUTPQR=54,
        noOfTVM=54, noOfTotal_QR=216, noOfONDCQR=54)
    for series in payload(snapshot_bundle, "hourlybaseddata")["series"]:
        if series["name"] != "Total":
            series["data"][3] = counts.get(series["name"], 0)
    for line in payload(snapshot_bundle, "stationData"):
        for series in line["series"]:
            if series["name"] != "Total":
                series["data"] = [counts.get(series["name"], 0) // 54] * len(line["categories"])
    assert collect_snapshot(snapshot_bundle).exit_code == 0


def test_api_service_hour_labels_keep_their_wall_time_with_z_suffix(snapshot_bundle, collect_snapshot):
    hourly = payload(snapshot_bundle, "hourlybaseddata")
    hourly["categories"] = [value + ".000Z" for value in hourly["categories"]]
    assert collect_snapshot(snapshot_bundle).exit_code == 0


def test_archiving_does_not_invent_a_missing_capture_timestamp(snapshot_bundle, collect_snapshot, tmp_path):
    del snapshot_bundle["recorded_at_utc"]
    result = collect_snapshot(snapshot_bundle)
    saved = tmp_path / "output" / result.raw_paths[0]
    replay = ridership.collect_ridership(api_response=saved, output_dir=tmp_path / "replay")
    assert replay.exit_code == 2 and not replay.published
    assert any(f["code"] == "missing_or_invalid_capture_timestamp" for f in replay.findings)


def test_endpoint_capture_times_cannot_escape_the_attempt(snapshot_bundle, collect_snapshot):
    snapshot_bundle["response_timestamps"] = {
        url: {"started_at_utc": snapshot_bundle["recorded_at_utc"],
              "finished_at_utc": "2026-11-07T18:31:00+00:00"}
        for url in snapshot_bundle["responses"]}
    result = collect_snapshot(snapshot_bundle)
    assert result.exit_code == 2 and not result.published
    assert any(f["code"] == "invalid_response_timestamps" for f in result.findings)


def test_previously_published_extra_fare_cannot_silently_disappear(snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    path = tmp_path / "output" / ridership.DAILY_FILENAME
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame["noOfFutureQR"] = "1"
    frame.to_csv(path, index=False)
    snapshot_bundle["recorded_at_utc"] = "2026-11-07T05:00:00+00:00"
    before = csvs(tmp_path)
    result = collect_snapshot(snapshot_bundle)
    assert result.exit_code == 2 and not result.published
    assert any(f["code"] == "missing_fare" and f["column"] == "noOfFutureQR" for f in result.findings)
    assert csvs(tmp_path) == before


@pytest.mark.parametrize("change,code", [
    ("row", "row_total"), ("qr", "qr_subtotal"), ("ondc", "ondc_subtotal"),
    ("daily", "daily_components"), ("station", "daily_station_total"),
    ("unknown", "unmapped_fare"),
])
@pytest.mark.parametrize("day", ["0", "1"])
def test_arithmetic_fallback_is_live_only(change, code, day, snapshot_bundle, collect_snapshot, tmp_path):
    bundle = live_bundle(snapshot_bundle) if day == "0" else snapshot_bundle
    if change == "row":
        payload(bundle, "hourlybaseddata")["series"][0]["data"][0] = 1
    elif change == "qr":
        payload(bundle, "allTicketCount")["noOfTotal_QR"] = 1
    elif change == "ondc":
        payload(bundle, "allTicketCount")["noOfONDCQR"] = 1
    elif change == "daily":
        payload(bundle, "allTicketCount")["totalTickets"] = 271
    elif change == "station":
        line = payload(bundle, "stationData")[0]
        line["series"][0]["data"][0] += 1
        next(s for s in line["series"] if s["name"] == "Singara Chennai Card")["data"][0] += 1
    else:
        payload(bundle, "allTicketCount")["noOfFutureQR"] = 1
    result = collect_snapshot(bundle, day=day)
    assert result.exit_code == 2
    assert any(f["code"] == code for f in result.findings)
    assert result.published == (day == "0")
    assert len(csvs(tmp_path)) == (3 if day == "0" else 0)
    assert records(tmp_path)[-1]["validation_passed"] is False
    assert records(tmp_path)[-1]["selected_attempt"] == (2 if day == "0" else None)


def test_live_missing_rows_do_not_force_publish(snapshot_bundle, collect_snapshot, tmp_path):
    bundle = live_bundle(snapshot_bundle)
    payload(bundle, "stationData").pop()
    result = collect_snapshot(bundle, day="0")
    assert result.status == "pending_retry"
    assert not result.published
    assert not csvs(tmp_path)


def test_live_hour_coverage_only_requires_elapsed_core_hours(snapshot_bundle, collect_snapshot):
    bundle = live_bundle(snapshot_bundle, "2026-11-06T00:00:00+00:00")  # 05:30 IST
    for hour in sorted(CORE_HOURS - {"00:00", "04:00", "05:00"}):
        drop_hour(bundle, hour)
    hourly = payload(bundle, "hourlybaseddata")
    for series in hourly["series"]:
        if series["name"] in ("Total", "Singara Chennai Card"):
            series["data"][1] = 270
    assert collect_snapshot(bundle, day="0").exit_code == 0
    drop_hour(bundle, "04:00")
    assert collect_snapshot(bundle, day="0").exit_code == 2


def test_previous_nonzero_optional_hour_cannot_disappear(snapshot_bundle, collect_snapshot, tmp_path):
    first = copy.deepcopy(snapshot_bundle)
    hourly = payload(first, "hourlybaseddata")
    hourly["categories"].append("2026-11-06T01:00:00")
    for series in hourly["series"]:
        active = series["name"] in ("Total", "Singara Chennai Card")
        series["data"].append(2 if active else 0)
        if active:
            series["data"][3] -= 2  # 06:00
    assert collect_snapshot(first).exit_code == 0
    before = csvs(tmp_path)
    snapshot_bundle["recorded_at_utc"] = "2026-11-07T05:00:00+00:00"
    result = collect_snapshot(snapshot_bundle)
    assert result.exit_code == 2
    assert "01:00" in next(f["missing"] for f in result.findings if f["code"] == "missing_hours")
    assert csvs(tmp_path) == before


def test_historical_roster_and_optional_modes(snapshot_bundle, collect_snapshot, tmp_path):
    bundle = copy.deepcopy(snapshot_bundle)
    bundle["recorded_at_utc"] = "2026-10-13T04:00:00+00:00"
    daily = payload(bundle, "allTicketCount")
    daily.update(totalTickets=215, noOfNCMCcard=215)
    del daily["noOfUTPQR"], daily["noOfTVM"]
    station = payload(bundle, "stationData")
    station.pop()
    hourly = payload(bundle, "hourlybaseddata")
    hourly["categories"] = [s.replace("2026-11-06", "2026-10-12") for s in hourly["categories"]]
    for line in [hourly, *station]:
        line["series"] = [s for s in line["series"] if s["name"] not in ("TVM", "UTP QR")]
    for series in hourly["series"]:
        if series["name"] in ("Total", "Singara Chennai Card"):
            series["data"][3] = 215
    assert collect_snapshot(bundle).exit_code == 0
    for filename in ridership.FILENAMES:
        path = tmp_path / "output" / filename
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        frame["noOfUTPQR" if filename == ridership.DAILY_FILENAME else "UTP QR"] = "0"
        frame.to_csv(path, index=False)
    assert collect_snapshot(bundle).exit_code == 0


def test_retry_refetches_all_endpoints_and_archives_both_attempts(snapshot_bundle, snapshot_clock, tmp_path):
    bad = copy.deepcopy(snapshot_bundle)
    payload(bad, "stationData").pop()
    with responses.RequestsMock() as mock:
        for bundle in (bad, snapshot_bundle):
            for url, data in bundle["responses"].items():
                mock.get(url, json=data)
        result = ridership.collect_ridership(output_dir=tmp_path / "output")
        assert len(mock.calls) == 6
    assert result.exit_code == 0
    ridership.sleep.assert_called_once_with(60)
    assert len(result.raw_paths) == 2
    archived = [json.loads((tmp_path / "output" / path).read_text()) for path in result.raw_paths]
    assert archived[0]["responses"] == bad["responses"]
    assert archived[1]["responses"] == snapshot_bundle["responses"]
    assert records(tmp_path)[-1]["selected_attempt"] == 2


def test_real_captures_remain_negative_validation_cases(tmp_path, snapshot_clock):
    result = ridership.collect_ridership(api_response=FIXTURES / "api/day1.json", output_dir=tmp_path / "output")
    assert result.exit_code == 2
    assert any(f["code"] == "daily_station_total" for f in result.findings)
    assert not csvs(tmp_path)


def test_original_captured_payload_still_normalizes_to_original_expected_csvs():
    bundle = json.loads((FIXTURES / "api/day1.json").read_text())
    daily, hourly, station = [bundle["responses"][ridership.endpoint(name, "1")]
                               for name in ("allTicketCount", "hourlybaseddata", "stationData")]
    date = ridership.extract_dataset_date(hourly)
    actual = [ridership.normalize_daily(daily, date), ridership.normalize_hourly(hourly),
              ridership.normalize_station(station, date)]
    for frame, filename, schema in zip(actual, ridership.FILENAMES, ridership.SCHEMAS):
        expected = pd.read_csv(FIXTURES / "expected/Ridership" / Path(filename).name,
                               dtype=str, keep_default_na=False)
        keys = list(schema.key_columns)
        assert_frame_equal(frame.astype(str).sort_values(keys).reset_index(drop=True),
                           expected.sort_values(keys).reset_index(drop=True))


@pytest.mark.parametrize("change,code", [
    ("daily_shape", "invalid_daily_shape"), ("hourly_shape", "invalid_series_shape"),
    ("station_shape", "invalid_station_shape"), ("series_entry", "invalid_series_shape"),
    ("station_name", "invalid_station"), ("duplicate_line", "duplicate_line"),
    ("unknown_line", "unexpected_line"), ("unknown_series", "unmapped_fare"),
    ("numeric_timestamp", "invalid_timestamp"), ("naive_capture", "missing_or_invalid_capture_timestamp"),
    ("legacy_card", "unmapped_fare"), ("half_hour", "invalid_timestamp"),
])
def test_additional_schema_failures(change, code, snapshot_bundle, collect_snapshot):
    h = payload(snapshot_bundle, "hourlybaseddata")
    s = payload(snapshot_bundle, "stationData")
    if change in ("daily_shape", "hourly_shape", "station_shape"):
        endpoint = {"daily_shape": "allTicketCount", "hourly_shape": "hourlybaseddata", "station_shape": "stationData"}[change]
        snapshot_bundle["responses"][ridership.endpoint(endpoint, "1")] = None
    elif change == "series_entry":
        h["series"].append({"name": "bad", "data": None})
    elif change == "station_name":
        s[0]["categories"][0] = 1
    elif change == "duplicate_line":
        s.append({"line": "01", "categories": [], "series": [
            {"name": series["name"], "data": []} for series in s[0]["series"]]})
    elif change == "unknown_line":
        s[0]["line"] = "99"
    elif change == "unknown_series":
        h["series"].append({"name": "Future QR", "data": [0] * len(h["categories"])})
    elif change == "numeric_timestamp":
        h["categories"][0] = 0
    elif change == "naive_capture":
        snapshot_bundle["recorded_at_utc"] = "2026-11-07T04:00:00"
    elif change == "legacy_card":
        payload(snapshot_bundle, "allTicketCount")["noOfTripcard"] = 1
    else:
        h["categories"][0] = "2026-11-06T00:30:00"
    result = collect_snapshot(snapshot_bundle)
    assert result.exit_code == 2
    assert any(f["code"] == code for f in result.findings)


def test_line04_capture_arithmetic_is_not_silently_corrected(tmp_path, snapshot_clock):
    bundle = json.loads((FIXTURES / "api/line04.json").read_text())
    bundle["recorded_at_utc"] = "2026-10-15T04:00:00+00:00"
    path = tmp_path / "input.json"
    path.write_text(json.dumps(bundle))
    result = ridership.collect_ridership(api_response=path, output_dir=tmp_path / "output")
    assert result.exit_code == 2
    assert not any(f["kind"] == "structural" for f in result.findings)
    assert any(f["code"] == "daily_hourly_total" and f["expected"] == 415539 and f["actual"] == 415856
               for f in result.findings)
    assert not csvs(tmp_path)


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
