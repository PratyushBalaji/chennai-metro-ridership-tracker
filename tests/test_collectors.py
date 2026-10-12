import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
import yaml


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


@pytest.mark.parametrize("script", FILES)
def test_cli_file_input_matches_expected_csvs(script, tmp_path, http_mock, run_collector):
    output_dir = tmp_path / "separate output"
    run_collector(script, "--api-response", FIXTURES / "api/day1.json",
                  "--output-dir", output_dir)
    assert len(http_mock.calls) == 0
    assert {path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*.csv")} == set(FILES[script])
    for filename in FILES[script]:
        assert_frame_equal(canonical(read_csv(output_dir / filename)),
                           canonical(read_csv(FIXTURES / "expected" / filename)))
        assert not (tmp_path / filename).exists()


@pytest.mark.parametrize("script", FILES)
@pytest.mark.parametrize("position", ("before_latest", "same_latest", "after_latest"))
def test_cli_file_input_with_existing_csv(script, position, tmp_path, http_mock, run_collector):
    output_dir = tmp_path / "output"
    before = {}
    for filename in FILES[script]:
        incoming = read_csv(FIXTURES / "expected" / filename)
        seed = read_csv(FIXTURES / "seed" / filename)
        if position == "same_latest":
            seed = pd.concat([seed[seed["Date"] == "2000-01-01"], incoming], ignore_index=True)
        elif position == "after_latest":
            seed = seed[seed["Date"] == "2000-01-01"].reset_index(drop=True)
        output = output_dir / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        seed.to_csv(output, index=False)
        before[filename] = seed

    for _ in range(2):
        run_collector(script, "--api-response", FIXTURES / "api/day1.json",
                      "--output-dir", output_dir)
        for filename, seed in before.items():
            incoming = read_csv(FIXTURES / "expected" / filename)
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
                           canonical(read_csv(FIXTURES / "expected" / filename)))


@pytest.mark.parametrize("workflow", ("daily_data_update.yml", "ridership_anomaly_retry.yml"))
def test_workflow_commits_valid_updates_and_anomalies_before_rebase(workflow, tmp_path, run_collector):
    git = shutil.which("git")
    bash = str(Path(git).parent.parent / "bin/bash.exe") if os.name == "nt" and git else shutil.which("bash")
    if not git or not bash or not Path(bash).exists():
        pytest.skip("git and bash are required for the workflow integration test")

    def command(*args, cwd):
        result = subprocess.run([git, *map(str, args)], cwd=cwd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    origin, first, runner = (tmp_path / name for name in ("origin.git", "first", "runner"))
    first.mkdir()
    command("init", "--bare", "-b", "main", origin, cwd=tmp_path)
    command("init", "-b", "main", cwd=first)
    command("config", "user.name", "test", cwd=first)
    command("config", "user.email", "test@example.com", cwd=first)
    shutil.copytree(FIXTURES / "idempotence/seed/Ridership", first / "Ridership")
    (first / "Parking").mkdir()
    shutil.copyfile(FIXTURES / "expected/Parking/ChennaiMetro_Daily_Parking.csv",
                    first / "Parking/ChennaiMetro_Daily_Parking.csv")
    command("add", ".", cwd=first)
    command("commit", "-m", "test: seed csvs", cwd=first)
    command("remote", "add", "origin", origin, cwd=first)
    command("push", "-u", "origin", "main", cwd=first)
    command("clone", origin, runner, cwd=tmp_path)

    cases = json.loads((FIXTURES / "idempotence/cases.json").read_text())["cases"]
    payload = copy.deepcopy(cases["mixed"])
    payload["hourly"]["categories"].append("2026-06-15T12:00:00")
    for series in payload["hourly"]["series"]:
        series["data"].append({"Total": 40, "Paper QR": 15, "Singara Chennai Card": 25}[series["name"]])
    base = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
    bundle = {"responses": {base + endpoint + "/0": payload[name] for endpoint, name in (
        ("allTicketCount", "daily"), ("hourlybaseddata", "hourly"), ("stationData", "station"),
    )}}
    response_file = tmp_path / "mixed.json"
    response_file.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        run_collector("ridership", "--day", "0", "--api-response", response_file, "--output-dir", runner)
    assert error.value.code == 2

    (first / "remote_change.txt").write_text("unrelated remote change\n")
    command("add", "remote_change.txt", cwd=first)
    command("commit", "-m", "test: remote change", cwd=first)
    command("push", cwd=first)

    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / ".github/workflows" / workflow).read_text())
    steps = next(iter(document["jobs"].values()))["steps"]
    commit_step = next(step for step in steps if step.get("name") == "Commit and push data")
    env = {**os.environ, "BRANCH_NAME": "main"}
    for _ in range(2):
        result = subprocess.run([bash, "-e", "-c", commit_step["run"]], cwd=runner,
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert command("status", "--porcelain", cwd=runner) == ""
    assert (runner / "remote_change.txt").read_text().strip() == "unrelated remote change"
    log = command("--git-dir", origin, "show", "main:Discrepancies/anomaly_log.jsonl", cwd=tmp_path)
    assert json.loads(log)["status"] == "pending_retry"
    hourly = command("--git-dir", origin, "show", "main:Ridership/ChennaiMetro_Hourly_Ridership.csv", cwd=tmp_path)
    assert "2026-06-15,12:00,40,15,25" in hourly
    report_step = steps[-1]
    reported = subprocess.run([bash, "-e", "-c", report_step["run"]], cwd=runner,
                              env={**env, "EXIT_CODE": "2"}, capture_output=True, text=True, timeout=30)
    assert reported.returncode == 2
