import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from unittest.mock import Mock

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
import requests

from collector import append_csv, normalize_parking_columns, read_csv, write_csv_atomic


FIXTURES = Path(__file__).parent / "fixtures"
API = "https://commuters-dataapi.chennaimetrorail.org/api/"
FILES = {
    "ridership": [f"Ridership/ChennaiMetro_{kind}_Ridership.csv" for kind in ("Daily", "Hourly", "Station")],
    "parking": [f"Parking/ChennaiMetro_{kind}_Parking.csv" for kind in ("Daily", "Hourly", "Station")],
    "phpdt": ["PHPDT/ChennaiMetro_Daily_PHPDT.csv"],
}


@pytest.fixture
def new_bundle():
    return json.loads((FIXTURES / "api/line04.json").read_text(encoding="utf-8"))


def response_file(tmp_path, bundle):
    path = tmp_path / "response.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


@pytest.mark.parametrize("script", ["ridership", "parking"])
@pytest.mark.parametrize("day,date", [("0", "2026-10-15"), ("1", "2026-10-14")])
def test_new_responses_preserve_old_columns_and_replay(script, day, date, tmp_path, run_collector, http_mock, new_bundle, monkeypatch):
    output = tmp_path / "output"
    run_collector(script, "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    old = {name: read_csv(output / name) for name in FILES[script]}
    # Main collects /1 only; replay the supplied /0 snapshot through the same parser.
    if day == "0":
        for url, payload in list(new_bundle["responses"].items()):
            if url.endswith("/0"):
                new_bundle["responses"][url[:-1] + "1"] = payload
    if script == "parking":
        monkeypatch.setenv("DATASET_DATE", date)
    path = response_file(tmp_path, new_bundle)
    run_collector(script, "--api-response", path, "--output-dir", output)
    first = {name: (output / name).read_bytes() for name in FILES[script]}
    run_collector(script, "--api-response", path, "--output-dir", output)
    assert first == {name: (output / name).read_bytes() for name in FILES[script]}
    for name, before in old.items():
        actual = read_csv(output / name)
        historical = actual.loc[actual["Date"] != date].reset_index(drop=True)
        assert_frame_equal(historical[before.columns], before)
        if script == "ridership":
            column = "noOfUTPQR" if "Daily" in name else "UTP QR"
            assert set(actual[column]) == {"0"}
    station = read_csv(output / FILES[script][2])
    station = station.loc[station["Date"] == date]
    assert station.groupby("Line").size().to_dict() == ({"01": 26, "02": 17, "04": 11} if script == "ridership" else {"01": 24, "02": 15, "04": 7})
    expected = {"PBYM", "PNMGH", "KRYCH", "KTTPK", "IYPTH", "THLYR", "PRJNC"}
    if script == "ridership":
        expected |= {"PNMBS", "KMNCH", "PRBYP", "SVA"}
        assert set(station.loc[station["Station"] == "SVA", "Line"]) == {"02", "04"}
        daily = read_csv(output / FILES[script][0]).iloc[-1]
        assert daily["noOfUberQR"] == ("7502" if day == "0" else "17143")
        assert daily["noOfWhatsAppQR"] == ("8359" if day == "0" else "15916")
    else:
        assert "Eight Wheleer" not in station
        assert "Eight Wheeler" in station
    assert set(station.loc[station["Line"] == "04", "Station"]) == expected
    assert not http_mock.calls


def test_new_mode_on_only_one_line_is_not_dropped(tmp_path, run_collector, new_bundle):
    payload = new_bundle["responses"][API + "PassengerFlow/stationData/1"]
    payload[2]["series"].append({"name": "Future QR", "data": [7] * 11})
    run_collector("ridership", "--api-response", response_file(tmp_path, new_bundle))
    station = read_csv(tmp_path / FILES["ridership"][2])
    assert set(station.loc[station["Line"] == "04", "Future QR"]) == {"7"}
    assert set(station.loc[station["Line"] != "04", "Future QR"]) == {""}


@pytest.mark.parametrize("legacy,current,expected", [("", "", ""), ("2", "", "2"), ("", "3", "3"), ("2", "2.0", "2.0"), (None, "0", "0")])
def test_parking_alias_coalescing(legacy, current, expected):
    result = normalize_parking_columns(pd.DataFrame([{"Eight Wheleer": legacy, "Eight Wheeler": current}]))
    assert result.to_dict("records") == [{"Eight Wheeler": expected}]


@pytest.mark.parametrize("side", ["archive", "payload"])
def test_parking_alias_conflict_leaves_all_csvs_unchanged(side, tmp_path, run_collector, new_bundle):
    output = tmp_path / "output"
    run_collector("parking", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    if side == "archive":
        station = read_csv(output / FILES["parking"][2])
        station["Eight Wheleer"] = "1"
        station.to_csv(output / FILES["parking"][2], index=False)
    else:
        line = new_bundle["responses"][API + "parkingdashboard/stationData/1"][0]
        line["series"].append({"name": "Eight Wheleer", "data": [1] * len(line["categories"])})
    before = {name: (output / name).read_bytes() for name in FILES["parking"]}
    with pytest.raises(ValueError, match="Conflicting Eight Wheleer"):
        run_collector("parking", "--api-response", response_file(tmp_path, new_bundle), "--output-dir", output)
    assert before == {name: (output / name).read_bytes() for name in FILES["parking"]}


def test_phpdt_empty_yellow_routes_then_same_day_completion(tmp_path, run_collector, new_bundle, capsys, http_mock):
    output = tmp_path / "output"
    path = response_file(tmp_path, new_bundle)
    run_collector("phpdt", "--api-response", path, "--output-dir", output)
    filename = output / FILES["phpdt"][0]
    old = read_csv(filename)
    assert len(old) == 82
    assert set(old["Line"]) == {"1", "2"}
    assert "No data available for route key 'poBtoSVAViewModel'" in capsys.readouterr().out
    # Synthetic yellow values exercise the schema, not a claim of real ridership.
    payload = new_bundle["responses"][API + "PassengerFlow/PHPDTreport/"]
    for key, corridor in [("poBtoSVAViewModel", "poB_SVA4"), ("svAtoPOBViewModel", "svA4_POB")]:
        payload[key] = [{"transfromdate": "2026-10-14T09:00:00", "transtodate": "2026-10-14T10:00:00", corridor: 100}]
    path = response_file(tmp_path, new_bundle)
    run_collector("phpdt", "--api-response", path, "--output-dir", output)
    actual = read_csv(filename)
    assert_frame_equal(actual.iloc[:82].reset_index(drop=True), old)
    yellow = actual.loc[actual["Line"] == "04"]
    assert yellow[["Direction", "Start Station", "End Station", "PHPDT"]].to_dict("records") == [
        {"Direction": "UP", "Start Station": "PBYM", "End Station": "SVA", "PHPDT": "100"},
        {"Direction": "DOWN", "Start Station": "SVA", "End Station": "PBYM", "PHPDT": "100"},
    ]
    first = filename.read_bytes()
    run_collector("phpdt", "--api-response", path, "--output-dir", output)
    assert filename.read_bytes() == first
    assert not http_mock.calls


def test_phpdt_unknown_nonempty_route_stops_before_writing(tmp_path, run_collector, new_bundle):
    output = tmp_path / "output"
    run_collector("phpdt", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    filename = output / FILES["phpdt"][0]
    before = filename.read_bytes()
    payload = new_bundle["responses"][API + "PassengerFlow/PHPDTreport/"]
    payload["unknownViewModel"] = copy.deepcopy(payload["saPtoSWDViewModel"])
    with pytest.raises(ValueError, match="Unknown non-empty PHPDT routes"):
        run_collector("phpdt", "--api-response", response_file(tmp_path, new_bundle), "--output-dir", output)
    assert filename.read_bytes() == before


def test_phpdt_duplicate_corridors_stop_before_writing(tmp_path, run_collector, new_bundle):
    output = tmp_path / "output"
    run_collector("phpdt", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    filename = output / FILES["phpdt"][0]
    before = filename.read_bytes()
    payload = new_bundle["responses"][API + "PassengerFlow/PHPDTreport/"]
    payload["saPtoSWDViewModel"] *= 2
    with pytest.raises(ValueError, match="duplicate corridors"):
        run_collector("phpdt", "--api-response", response_file(tmp_path, new_bundle), "--output-dir", output)
    assert filename.read_bytes() == before


def test_append_aligns_columns_and_only_backfills_requested_column(tmp_path):
    path = tmp_path / "data.csv"
    old = pd.DataFrame([{"Date": "2026-10-12", "Line": "04", "Uber": "12", "Other": ""}])
    incoming = pd.DataFrame([{"UTP QR": 0, "Uber": 15, "Date": "2026-10-13", "Line": "04", "Other": ""}])
    append_csv(path, old, incoming, zero_columns=("UTP QR",))
    assert read_csv(path).to_dict("records") == [
        {"Date": "2026-10-12", "Line": "04", "Uber": "12", "Other": "", "UTP QR": "0"},
        {"Date": "2026-10-13", "Line": "04", "Uber": "15", "Other": "", "UTP QR": "0"},
    ]


def test_failed_atomic_write_keeps_original_and_cleans_temporary_file(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("Date,Total\n2026-10-12,10\n")
    before = path.read_bytes()
    with patch.object(pd.DataFrame, "to_csv", side_effect=OSError("disk error")):
        with pytest.raises(OSError, match="disk error"):
            write_csv_atomic(path, pd.DataFrame([{"Total": 11}]))
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_atomic_replace_keeps_original_and_cleans_temporary_file(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("Date,Total\n2026-10-12,10\n")
    before = path.read_bytes()
    with patch.object(Path, "replace", side_effect=OSError("replace error")):
        with pytest.raises(OSError, match="replace error"):
            write_csv_atomic(path, pd.DataFrame([{"Total": 11}]))
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("script", FILES)
def test_http_errors_are_checked_before_json_or_csv_writes(script, tmp_path, run_collector, monkeypatch):
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("503")
    get = Mock(return_value=response)
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(requests.HTTPError):
        run_collector(script)
    assert get.call_count == 1
    assert get.call_args.kwargs == {"timeout": 30}
    response.json.assert_not_called()
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.parametrize("script", FILES)
def test_root_cli_without_inherited_pythonpath(script, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON") and key != "DATASET_DATE"}
    result = subprocess.run([sys.executable, "-E", str(root / (script + ".py")),
                             "--api-response", str(FIXTURES / "api/line04.json"),
                             "--output-dir", str(tmp_path / "output")],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "output").rglob("*.csv"))) == len(FILES[script])
