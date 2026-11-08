import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
import requests
import yaml

from collector import fetch_json
from collector.anomalies import read_last_record


FIXTURES = Path(__file__).parent / "fixtures"
FILES = {
    "ridership": (
        "ridership/ChennaiMetro_Daily_Ridership.csv",
        "ridership/ChennaiMetro_Hourly_Ridership.csv",
        "ridership/ChennaiMetro_Station_Ridership.csv",
    ),
    "parking": (
        "parking/ChennaiMetro_Daily_Parking.csv",
        "parking/ChennaiMetro_Hourly_Parking.csv",
        "parking/ChennaiMetro_Station_Parking.csv",
    ),
    "phpdt": ("phpdt/ChennaiMetro_Daily_PHPDT.csv",),
}


def read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def canonical(frame):
    frame = frame.fillna("")
    keys = [column for column in frame.columns if column in (
        "Date", "Line", "Direction", "Hour", "Station", "Start Hour",
        "End Hour", "Start Station", "End Station",
    )]
    return frame.sort_values(keys).reset_index(drop=True)


def expected_csv(filename, seed=False):
    # Captured ridership responses remain negative cases; success uses a complete snapshot.
    if filename in FILES["ridership"] and not seed:
        folder = FIXTURES / "snapshot_expected"
    elif filename == FILES["parking"][2]:
        folder = FIXTURES / "canonical"
        if seed:
            folder /= "seed"
    else:
        folder = FIXTURES / ("seed" if seed else "expected")
    frame = read_csv(folder / legacy_fixture_path(filename))
    if seed and filename in FILES["ridership"]:
        frame["noOfUTPQR" if "Daily" in filename else "UTP QR"] = "0"
    return frame


def api_fixture(script):
    return FIXTURES / "api" / ("consistent_ridership.json" if script == "ridership" else "day1.json")


@pytest.mark.parametrize("script", ["parking", "phpdt"])
def test_imports_do_not_collect_parse_args_or_write(script, monkeypatch, tmp_path):
    import argparse

    monkeypatch.chdir(tmp_path)
    forbidden = Mock(side_effect=AssertionError("import must be side-effect free"))
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", forbidden)
    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(os, "makedirs", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(pd.DataFrame, "to_csv", forbidden)
    monkeypatch.delitem(sys.modules, f"collector.{script}", raising=False)
    importlib.import_module(f"collector.{script}")
    forbidden.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_shared_fetch_checks_status_and_timeout(monkeypatch):
    response = Mock()
    response.json.return_value = {"data": 1}
    get = Mock(return_value=response)
    monkeypatch.setattr(requests, "get", get)
    assert fetch_json("https://example.test/data") == {"data": 1}
    get.assert_called_once_with("https://example.test/data", timeout=30)
    response.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize("error", [requests.Timeout("slow"), requests.HTTPError("503")])
def test_shared_fetch_propagates_http_failures(error, monkeypatch):
    response = Mock()
    if isinstance(error, requests.Timeout):
        get = Mock(side_effect=error)
    else:
        response.raise_for_status.side_effect = error
        get = Mock(return_value=response)
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(type(error)):
        fetch_json("https://example.test/data")
    get.assert_called_once_with("https://example.test/data", timeout=30)
    response.json.assert_not_called()


@pytest.mark.parametrize("bundle,message", [
    ([], "responses.*object"), ({}, "responses.*object"),
    ({"responses": []}, "responses.*object"), ({"responses": {}}, "no response"),
])
def test_shared_fetch_validates_fixture_bundles(bundle, message, tmp_path, monkeypatch):
    path = tmp_path / "response.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    forbidden = Mock(side_effect=AssertionError("fixture mode must not use HTTP"))
    monkeypatch.setattr(requests, "get", forbidden)
    with pytest.raises(ValueError, match=message):
        fetch_json("https://example.test/data", path)
    forbidden.assert_not_called()


@pytest.mark.parametrize("script", FILES)
def test_root_cli_without_pythonpath(script, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON") and key != "DATASET_DATE"}
    output = tmp_path / "output"
    result = subprocess.run([sys.executable, "-E", "-m", f"collector.{script}",
                             "--api-response", str(api_fixture(script)),
                             "--output-dir", str(output)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    for filename in FILES[script]:
        assert_frame_equal(canonical(read_csv(output / filename)), canonical(expected_csv(filename)))


@pytest.mark.parametrize("script", ["parking", "phpdt"])
def test_a_b_a_replay_preserves_history(script, tmp_path, run_collector, api_bundle, monkeypatch):
    original = copy.deepcopy(api_bundle)
    response_file = tmp_path / "response.json"
    output = tmp_path / "output"
    after_a = {}
    for date in ("2026-10-11", "2026-10-12", "2026-10-11"):
        bundle = copy.deepcopy(original)
        if script == "parking":
            monkeypatch.setenv("DATASET_DATE", date)
        else:
            for entries in bundle["responses"]["https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/PHPDTreport/"].values():
                for entry in entries:
                    for field in ("transfromdate", "transtodate"):
                        entry[field] = date + entry[field][10:]
        response_file.write_text(json.dumps(bundle), encoding="utf-8")
        run_collector(script, "--api-response", response_file, "--output-dir", output)
        if not after_a:
            after_a = {filename: read_csv(output / filename) for filename in FILES[script]}
    for filename, original_frame in after_a.items():
        actual = read_csv(output / filename)
        assert len(actual) == 2 * len(original_frame)
        assert_frame_equal(actual[actual["Date"] == "2026-10-11"].reset_index(drop=True), original_frame)
        if script == "parking" and "Line" in actual:
            assert set(actual["Line"]) == {"01", "02"}
    assert not (output / "discrepancies").exists()


@pytest.mark.parametrize("script", ["parking", "phpdt"])
def test_partial_response_can_be_completed(script, tmp_path, run_collector, api_bundle):
    partial = copy.deepcopy(api_bundle)
    if script == "parking":
        for endpoint in ("hourlybaseddata", "stationData"):
            payload = partial["responses"][f"https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/{endpoint}/1"]
            lines = payload if isinstance(payload, list) else [payload]
            for line in lines:
                line["categories"] = line["categories"][:-1]
                for series in line["series"]:
                    series["data"] = series["data"][:-1]
    else:
        del partial["responses"]["https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/PHPDTreport/"]["saPtoSWDViewModel"][0]["saP_SME"]
    response_file = tmp_path / "partial.json"
    response_file.write_text(json.dumps(partial), encoding="utf-8")
    output = tmp_path / "output"
    run_collector(script, "--api-response", response_file, "--output-dir", output)
    run_collector(script, "--api-response", api_fixture(script), "--output-dir", output)
    for filename in FILES[script]:
        assert_frame_equal(canonical(read_csv(output / filename)), canonical(expected_csv(filename)))


@pytest.mark.parametrize("change", ["increase", "lower", "mixed", "missing", "backfill"])
def test_parking_strict_upsert_and_anomaly_log(change, tmp_path, run_collector, api_bundle):
    output = tmp_path / "output"
    run_collector("parking", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    before = read_csv(output / FILES["parking"][0])
    daily = api_bundle["responses"]["https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/allTicketCount/1"]
    if change in ("increase", "mixed"):
        daily["totalVehicles"] += 1
    if change in ("lower", "mixed"):
        daily["twoWheeler"] -= 1
    if change == "missing":
        del daily["twoWheeler"]
    if change == "backfill":
        before.loc[0, "twoWheeler"] = ""
        before.to_csv(output / FILES["parking"][0], index=False)
    # A valid hourly update must still be saved when the daily row is anomalous.
    hourly = api_bundle["responses"]["https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/hourlybaseddata/1"]
    next(series for series in hourly["series"] if series["name"] == "Bike")["data"][0] += 1
    response_file = tmp_path / "changed.json"
    response_file.write_text(json.dumps(api_bundle), encoding="utf-8")
    if change in ("lower", "mixed", "missing"):
        with pytest.raises(SystemExit) as error:
            run_collector("parking", "--api-response", response_file, "--output-dir", output)
        assert error.value.code == 2
        assert_frame_equal(read_csv(output / FILES["parking"][0]), before)
        log = output / "discrepancies/anomaly_log.jsonl"
        record = read_last_record(log)
        assert (record["source"], record["day"], record["status"], record["date"]) == ("parking", "1", "needs_review", "2026-10-11")
        previous = log.read_bytes()
        with pytest.raises(SystemExit):
            run_collector("parking", "--api-response", response_file, "--output-dir", output)
        assert log.read_bytes().startswith(previous)
        assert len(log.read_text().splitlines()) == 2
    else:
        run_collector("parking", "--api-response", response_file, "--output-dir", output)
        assert read_csv(output / FILES["parking"][0]).iloc[0]["Total Vehicles"] == str(daily["totalVehicles"])
        assert read_csv(output / FILES["parking"][0]).iloc[0]["twoWheeler"] == str(daily["twoWheeler"])
    assert read_csv(output / FILES["parking"][1]).iloc[0]["Bike"] == "4"


def test_parking_category_changes_align_by_name(tmp_path, run_collector, api_bundle):
    output = tmp_path / "output"
    run_collector("parking", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    snapshots = {filename: read_csv(output / filename) for filename in FILES["parking"]}
    base = "https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/"
    api_bundle["responses"][base + "allTicketCount/1"]["newCategory"] = 2
    for endpoint in ("hourlybaseddata", "stationData"):
        payload = api_bundle["responses"][base + endpoint + "/1"]
        for line in payload if isinstance(payload, list) else [payload]:
            line["series"].reverse()
            line["series"].append({"name": "newCategory", "data": [2] * len(line["categories"])})
    response_file = tmp_path / "category.json"
    response_file.write_text(json.dumps(api_bundle), encoding="utf-8")
    run_collector("parking", "--api-response", response_file, "--output-dir", output)
    for filename, before in snapshots.items():
        actual = read_csv(output / filename)
        assert_frame_equal(actual[before.columns], before)
        assert set(actual["newCategory"]) == {"2"}


@pytest.mark.parametrize("side", ["archive", "payload"])
def test_parking_alias_conflict_prevents_all_csv_writes(side, tmp_path, run_collector, api_bundle):
    output = tmp_path / "output"
    run_collector("parking", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    if side == "archive":
        station = read_csv(output / FILES["parking"][2])
        station["Eight Wheleer"] = "1"
        station.to_csv(output / FILES["parking"][2], index=False)
    else:
        line = api_bundle["responses"]["https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/stationData/1"][0]
        line["series"].append({"name": "Eight Wheeler", "data": [1] * len(line["categories"])})
    api_bundle["responses"]["https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/allTicketCount/1"]["totalVehicles"] += 1
    snapshots = {filename: (output / filename).read_bytes() for filename in FILES["parking"]}
    response_file = tmp_path / "conflict.json"
    response_file.write_text(json.dumps(api_bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting values.*Eight Wheleer.*Eight Wheeler"):
        run_collector("parking", "--api-response", response_file, "--output-dir", output)
    for filename, before in snapshots.items():
        assert (output / filename).read_bytes() == before
    assert not (output / "discrepancies").exists()


@pytest.mark.parametrize("change", ["higher_count", "lower_count", "start_hour", "end_hour"])
def test_phpdt_corrections_are_logged_without_creating_records(change, tmp_path, run_collector, api_bundle):
    output = tmp_path / "output"
    run_collector("phpdt", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    before = read_csv(output / FILES["phpdt"][0])
    entry = api_bundle["responses"]["https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/PHPDTreport/"]["saPtoSWDViewModel"][0]
    if change == "higher_count":
        entry["saP_SME"] += 1
    elif change == "lower_count":
        entry["saP_SME"] -= 1
    elif change == "start_hour":
        entry["transfromdate"] = "2026-10-11T18:00:00"
    else:
        entry["transtodate"] = "2026-10-11T21:00:00"
    response_file = tmp_path / "correction.json"
    response_file.write_text(json.dumps(api_bundle), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        run_collector("phpdt", "--api-response", response_file, "--output-dir", output)
    assert error.value.code == 2
    assert_frame_equal(read_csv(output / FILES["phpdt"][0]), before)
    record = read_last_record(output / "discrepancies/anomaly_log.jsonl")
    assert (record["source"], record["status"], record["date"]) == ("phpdt", "needs_review", "2026-10-11")
    assert "Start Hour" not in record["anomalies"][0]["key"]
    assert "End Hour" not in record["anomalies"][0]["key"]


def test_phpdt_cli_fills_blank_counts_and_hours(tmp_path, run_collector):
    output = tmp_path / "output"
    run_collector("phpdt", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    frame = read_csv(output / FILES["phpdt"][0])
    frame.loc[0, ["PHPDT", "Start Hour", "End Hour"]] = ""
    frame.to_csv(output / FILES["phpdt"][0], index=False)
    run_collector("phpdt", "--api-response", FIXTURES / "api/day1.json", "--output-dir", output)
    assert_frame_equal(canonical(read_csv(output / FILES["phpdt"][0])), canonical(expected_csv(FILES["phpdt"][0])))
    assert not (output / "discrepancies").exists()


@pytest.mark.parametrize("workflow,step_name", [
    ("daily_data_update.yml", "Run ridership"), ("daily_data_update.yml", "Run parking"),
    ("ridership_anomaly_retry.yml", "Run retry"), ("daily_phpdt_update.yml", "Run PHPDT"),
])
@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_workflow_captures_python_exit_under_bash_e(workflow, step_name, exit_code, tmp_path):
    git = shutil.which("git")
    bash = str(Path(git).parent.parent / "bin/bash.exe") if os.name == "nt" and git else shutil.which("bash")
    if not bash or not Path(bash).exists():
        pytest.skip("bash is required for workflow tests")
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / ".github/workflows" / workflow).read_text())
    steps = next(iter(document["jobs"].values()))["steps"]
    run = next(step["run"] for step in steps if step.get("name") == step_name)
    csv_path = tmp_path / "data" / FILES["ridership"][1]
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("Date,Hour,Total\n2026-10-11,23:00,100\n")
    output = tmp_path / "github-output"
    env = {**os.environ, "GITHUB_OUTPUT": str(output), "DAY_SELECTOR": "1", "COLLECTION_EXIT": str(exit_code)}
    fake_python = 'python() { return "$COLLECTION_EXIT"; };\n'
    result = subprocess.run([bash, "-e", "-c", fake_python + run], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == f"exit_code={exit_code}"


def test_workflows_share_branch_lock_and_collector_requirements():
    root = Path(__file__).resolve().parents[1]
    for workflow in ("daily_data_update.yml", "ridership_anomaly_retry.yml", "daily_phpdt_update.yml"):
        document = yaml.safe_load((root / ".github/workflows" / workflow).read_text())
        assert document["concurrency"] == {"group": "data-update-${{ github.ref }}", "cancel-in-progress": False}
        steps = next(iter(document["jobs"].values()))["steps"]
        assert next(step["run"] for step in steps if step.get("name") == "Install reqs") == "python -m pip install ."
        assert steps[0]["with"]["ref"] == "${{ github.ref_name }}"
        commit = next(step for step in steps if step.get("name") == "Commit and push data")
        assert "== '0'" in commit["if"] and "== '2'" in commit["if"]
        if workflow == "daily_data_update.yml":
            assert "steps.parking.outputs.exit_code" in commit["if"]
            assert steps[-1]["env"]["PARKING_EXIT"] == "${{ steps.parking.outputs.exit_code }}"
            parking = next(step for step in steps if step.get("name") == "Run parking")
            assert parking["if"] == "env.DAY_SELECTOR == '1'"
            assert "DATASET_DATE" not in parking["run"]
    import tomllib
    config = tomllib.loads((root / "pyproject.toml").read_text())
    assert set(config["project"]["dependencies"]) == {"pandas", "requests"}
    assert set(config["project"]["optional-dependencies"]["notebook"]) == {"matplotlib", "plotly", "seaborn", "nbformat", "folium"}



@pytest.mark.parametrize("script", FILES)
def test_api_response_to_csv_matches_expected(script, tmp_path, http_mock, run_collector):
    run_collector(script)
    assert len(http_mock.calls) == {"ridership": 3, "parking": 4, "phpdt": 1}[script]
    assert {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.csv")} == set(FILES[script])
    for filename in FILES[script]:
        assert_frame_equal(canonical(read_csv(tmp_path / filename)),
                           canonical(expected_csv(filename)))


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
        incoming = expected_csv(filename)
        seed = read_csv(FIXTURES / "seed" / legacy_fixture_path(filename))
        if position == "same_latest":
            seed = pd.concat([seed[seed["Date"] == "2000-01-01"], incoming], ignore_index=True)
        elif position == "after_latest":
            seed = seed[seed["Date"] == "2000-01-01"].reset_index(drop=True)
        seed.to_csv(tmp_path / filename, index=False)
        expected_seed = expected_csv(filename, seed=True)
        if position == "same_latest":
            expected_seed = pd.concat([expected_seed[expected_seed["Date"] == "2000-01-01"], incoming], ignore_index=True)
        elif position == "after_latest":
            expected_seed = expected_seed[expected_seed["Date"] == "2000-01-01"].reset_index(drop=True)
        before[filename] = seed if position == "same_latest" and script == "ridership" else expected_seed

    run_collector(script)
    for filename, seed in before.items():
        incoming = expected_csv(filename)
        expected = seed if position == "same_latest" else pd.concat([seed, incoming], ignore_index=True)
        assert_frame_equal(canonical(read_csv(tmp_path / filename)), canonical(expected))


@pytest.mark.parametrize("script", FILES)
def test_empty_existing_csv_is_populated(script, tmp_path, http_mock, run_collector):
    for filename in FILES[script]:
        (tmp_path / filename).touch()
    run_collector(script)
    for filename in FILES[script]:
        assert_frame_equal(canonical(read_csv(tmp_path / filename)),
                           canonical(expected_csv(filename)))


def test_parking_can_use_dataset_date_from_workflow(tmp_path, http_mock, run_collector, monkeypatch):
    monkeypatch.setenv("DATASET_DATE", "2026-10-11")
    run_collector("parking")
    assert len(http_mock.calls) == 3
    for filename in FILES["parking"]:
        assert_frame_equal(canonical(read_csv(tmp_path / filename)),
                           canonical(expected_csv(filename)))


@pytest.mark.parametrize("script", FILES)
def test_cli_file_input_matches_expected_csvs(script, tmp_path, http_mock, run_collector):
    output_dir = tmp_path / "separate output"
    run_collector(script, "--api-response", api_fixture(script),
                  "--output-dir", output_dir)
    assert len(http_mock.calls) == 0
    assert {path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*.csv")} == set(FILES[script])
    for filename in FILES[script]:
        assert_frame_equal(canonical(read_csv(output_dir / filename)),
                           canonical(expected_csv(filename)))
        assert not (tmp_path / filename).exists()


@pytest.mark.parametrize("script", FILES)
@pytest.mark.parametrize("position", ("before_latest", "same_latest", "after_latest"))
def test_cli_file_input_with_existing_csv(script, position, tmp_path, http_mock, run_collector):
    output_dir = tmp_path / "output"
    before = {}
    for filename in FILES[script]:
        incoming = expected_csv(filename)
        seed = read_csv(FIXTURES / "seed" / legacy_fixture_path(filename))
        if position == "same_latest":
            seed = pd.concat([seed[seed["Date"] == "2000-01-01"], incoming], ignore_index=True)
        elif position == "after_latest":
            seed = seed[seed["Date"] == "2000-01-01"].reset_index(drop=True)
        output = output_dir / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        seed.to_csv(output, index=False)
        expected_seed = expected_csv(filename, seed=True)
        if position == "same_latest":
            expected_seed = pd.concat([expected_seed[expected_seed["Date"] == "2000-01-01"], incoming], ignore_index=True)
        elif position == "after_latest":
            expected_seed = expected_seed[expected_seed["Date"] == "2000-01-01"].reset_index(drop=True)
        before[filename] = seed if position == "same_latest" and script == "ridership" else expected_seed

    for _ in range(2):
        run_collector(script, "--api-response", api_fixture(script),
                      "--output-dir", output_dir)
        for filename, seed in before.items():
            incoming = expected_csv(filename)
            expected = seed if position == "same_latest" else pd.concat([seed, incoming], ignore_index=True)
            assert_frame_equal(canonical(read_csv(output_dir / filename)), canonical(expected))
    assert len(http_mock.calls) == 0


def test_parking_cli_file_input_with_workflow_date(tmp_path, http_mock, run_collector, monkeypatch, api_bundle):
    import json

    bundle = {"responses": {url: payload for url, payload in api_bundle["responses"].items()
                            if "/parkingdashboard/" in url}}
    response_file = tmp_path / "parking-only.json"
    response_file.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setenv("DATASET_DATE", "2026-10-11")
    output_dir = tmp_path / "output"
    run_collector("parking", "--api-response", response_file, "--output-dir", output_dir)
    assert len(http_mock.calls) == 0
    for filename in FILES["parking"]:
        assert_frame_equal(canonical(read_csv(output_dir / filename)),
                           canonical(expected_csv(filename)))


@pytest.mark.parametrize("workflow", ("daily_data_update.yml", "ridership_anomaly_retry.yml", "daily_phpdt_update.yml"))
@pytest.mark.parametrize("failure", (None, "commit", "rebase"))
def test_workflow_commits_valid_updates_and_anomalies_before_rebase(workflow, failure, tmp_path, run_collector, snapshot_bundle, snapshot_clock):
    git = shutil.which("git")
    bash = str(Path(git).parent.parent / "bin/bash.exe") if os.name == "nt" and git else shutil.which("bash")
    if not git or not bash or not Path(bash).exists():
        pytest.skip("git and bash are required for the workflow integration test")

    def command(*args, cwd):
        result = subprocess.run([git, *map(str, args)], cwd=cwd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    origin, first, runner = (tmp_path / name for name in ("origin.git", "first", "runner"))
    branch = "dev/fixture-check" if workflow == "daily_phpdt_update.yml" else "main"
    first.mkdir()
    command("init", "--bare", "-b", branch, origin, cwd=tmp_path)
    command("init", "-b", branch, cwd=first)
    command("config", "user.name", "test", cwd=first)
    command("config", "user.email", "test@example.com", cwd=first)
    shutil.copytree(FIXTURES / "idempotence/seed/Ridership", first / "data/ridership")
    (first / "data/parking").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "expected/Parking/ChennaiMetro_Daily_Parking.csv",
                    first / "data/parking/ChennaiMetro_Daily_Parking.csv")
    (first / "data/phpdt").mkdir(parents=True)
    phpdt_seed = expected_csv(FILES["phpdt"][0]).iloc[:-1]
    phpdt_seed.to_csv(first / "data" / FILES["phpdt"][0], index=False)
    command("add", ".", cwd=first)
    command("commit", "-m", "test: seed csvs", cwd=first)
    command("remote", "add", "origin", origin, cwd=first)
    command("push", "-u", "origin", branch, cwd=first)
    command("clone", origin, runner, cwd=tmp_path)

    bundle = copy.deepcopy(snapshot_bundle)
    bundle["day"] = "0"
    bundle["recorded_at_utc"] = "2026-11-06T12:00:00+00:00"
    bundle["responses"] = {url[:-1] + "0": data for url, data in bundle["responses"].items()}
    daily = next(data for url, data in bundle["responses"].items() if "allTicketCount" in url)
    daily["totalTickets"] += 1
    response_file = tmp_path / "mixed.json"
    response_file.write_text(json.dumps(bundle), encoding="utf-8")
    script, arguments = "ridership", ("--day", "0")
    if workflow == "daily_phpdt_update.yml":
        bundle = json.loads((FIXTURES / "api/day1.json").read_text())
        bundle["responses"]["https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/PHPDTreport/"]["saPtoSWDViewModel"][0]["saP_SME"] += 1
        response_file.write_text(json.dumps(bundle), encoding="utf-8")
        script, arguments = "phpdt", ()
    with pytest.raises(SystemExit) as error:
        run_collector(script, *arguments, "--api-response", response_file, "--output-dir", runner / "data")
    assert error.value.code == 2

    (first / "remote_change.txt").write_text("unrelated remote change\n")
    if failure == "rebase":
        conflict_file = FILES[script][0]
        (first / "data" / conflict_file).write_text("Date,Total\n2026-06-15,777\n")
        command("add", "data/" + conflict_file, cwd=first)
    command("add", "remote_change.txt", cwd=first)
    command("commit", "-m", "test: remote change", cwd=first)
    command("push", cwd=first)
    remote_before = command("--git-dir", origin, "rev-parse", branch, cwd=tmp_path)
    if failure == "commit":
        hook = runner / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / ".github/workflows" / workflow).read_text())
    steps = next(iter(document["jobs"].values()))["steps"]
    commit_step = next(step for step in steps if step.get("name") == "Commit and push data")
    env = {**os.environ, "BRANCH_NAME": branch, "RIDERSHIP_EXIT": "2", "PARKING_EXIT": ""}
    for _ in range(2):
        result = subprocess.run([bash, "-e", "-c", commit_step["run"]], cwd=runner,
                                env=env, capture_output=True, text=True, timeout=30)
        if failure:
            assert result.returncode != 0
            assert command("--git-dir", origin, "rev-parse", branch, cwd=tmp_path) == remote_before
            assert "data/discrepancies/anomaly_log.jsonl" not in command(
                "--git-dir", origin, "ls-tree", "-r", "--name-only", branch, cwd=tmp_path).splitlines()
            return
        assert result.returncode == 0, result.stderr
        assert command("status", "--porcelain", cwd=runner) == ""
    assert (runner / "remote_change.txt").read_text().strip() == "unrelated remote change"
    log = command("--git-dir", origin, "show", branch + ":data/discrepancies/anomaly_log.jsonl", cwd=tmp_path)
    assert json.loads(log)["status"] == ("needs_review" if script == "phpdt" else "published_provisional")
    if script == "phpdt":
        assert_frame_equal(read_csv(runner / "data" / FILES["phpdt"][0]), canonical(expected_csv(FILES["phpdt"][0])))
        pushed = command("--git-dir", origin, "show", branch + ":data/" + FILES["phpdt"][0], cwd=tmp_path)
        assert pushed == (runner / "data" / FILES["phpdt"][0]).read_text().strip()
    else:
        hourly = command("--git-dir", origin, "show", branch + ":data/ridership/ChennaiMetro_Hourly_Ridership.csv", cwd=tmp_path)
        assert "2026-11-06,06:00,270" in hourly
    report_step = steps[-1]
    reported = subprocess.run([bash, "-e", "-c", report_step["run"]], cwd=runner,
                              env={**env, "EXIT_CODE": "2"}, capture_output=True, text=True, timeout=30)
    assert reported.returncode == 2


@pytest.mark.parametrize("ridership_code", (0, 1, 2))
@pytest.mark.parametrize("parking_code", (0, 1, 2))
def test_daily_workflow_saves_each_collector_independently(ridership_code, parking_code, tmp_path):
    git = shutil.which("git")
    bash = str(Path(git).parent.parent / "bin/bash.exe") if os.name == "nt" and git else shutil.which("bash")
    if not git or not bash or not Path(bash).exists():
        pytest.skip("git and bash are required for workflow tests")

    def command(*args, cwd=tmp_path):
        result = subprocess.run([git, *map(str, args)], cwd=cwd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    origin, runner = tmp_path / "origin.git", tmp_path / "runner"
    runner.mkdir()
    command("init", "--bare", "-b", "main", origin)
    command("init", "-b", "main", cwd=runner)
    command("config", "user.name", "test", cwd=runner)
    command("config", "user.email", "test@example.com", cwd=runner)
    files = {name: runner / "data" / FILES[name][0] for name in ("ridership", "parking")}
    for path in files.values():
        path.parent.mkdir(parents=True)
        path.write_text("Date,Total\n2026-06-15,100\n")
    command("add", ".", cwd=runner)
    command("commit", "-m", "test: seed csvs", cwd=runner)
    command("remote", "add", "origin", origin, cwd=runner)
    command("push", "-u", "origin", "main", cwd=runner)

    codes = {"ridership": ridership_code, "parking": parking_code}
    for name, path in files.items():
        # Exit 1 may leave a partially written CSV; it must not be published.
        path.write_text(f"Date,Total\n2026-06-15,{999 if codes[name] == 1 else 150}\n")
    log = runner / "data/discrepancies/anomaly_log.jsonl"
    log.parent.mkdir()
    log.write_text('{"status":"needs_review"}\n')
    root = Path(__file__).resolve().parents[1]
    steps = yaml.safe_load((root / ".github/workflows/daily_data_update.yml").read_text())["jobs"]["update-data"]["steps"]
    commit = next(step for step in steps if step.get("name") == "Commit and push data")
    env = {**os.environ, "BRANCH_NAME": "main", "RIDERSHIP_EXIT": str(ridership_code), "PARKING_EXIT": str(parking_code)}
    if any(code in (0, 2) for code in codes.values()):
        result = subprocess.run([bash, "-e", "-c", commit["run"]], cwd=runner,
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert command("status", "--porcelain", cwd=runner) == ""
        assert json.loads(command("--git-dir", origin, "show", "main:data/discrepancies/anomaly_log.jsonl"))["status"] == "needs_review"
    for name, path in files.items():
        published = command("--git-dir", origin, "show", "main:" + path.relative_to(runner).as_posix())
        expected = 100 if codes[name] == 1 else 150
        assert published == f"Date,Total\n2026-06-15,{expected}"
    result = subprocess.run([bash, "-e", "-c", steps[-1]["run"]], cwd=runner,
                            env=env, capture_output=True, text=True, timeout=30)
    expected_code = 1 if 1 in codes.values() else 2 if 2 in codes.values() else 0
    assert result.returncode == expected_code, result.stderr


def legacy_fixture_path(filename):
    path = Path(filename)
    folders = {"ridership": "Ridership", "parking": "Parking", "phpdt": "PHPDT"}
    return Path(folders[path.parts[0]]) / path.name
