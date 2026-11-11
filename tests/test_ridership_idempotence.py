import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
import responses

from collector import ridership
from collector.anomalies import append_jsonl, read_last_record
from collector.csv import write_csv_group


pytestmark = pytest.mark.idempotence


def payload(bundle, name):
    return next(value for url, value in bundle["responses"].items() if url.endswith(name + "/" + bundle["day"]))


def live(bundle, recorded="2026-11-06T12:00:00+00:00"):
    bundle = copy.deepcopy(bundle)
    bundle.update(day="0", recorded_at_utc=recorded)
    bundle["responses"] = {url[:-1] + "0": data for url, data in bundle["responses"].items()}
    return bundle


def frames(tmp_path):
    return [pd.read_csv(tmp_path / "output" / name, dtype=str, keep_default_na=False) for name in ridership.FILENAMES]


def csv_bytes(tmp_path):
    return {path: path.read_bytes() for path in (tmp_path / "output").rglob("*.csv")}


def changed_counts(bundle, per_station):
    bundle = copy.deepcopy(bundle)
    bundle["recorded_at_utc"] = "2026-11-07T05:00:00+00:00"
    total = 54 * per_station
    payload(bundle, "allTicketCount").update(totalTickets=total, noOfNCMCcard=total)
    for series in payload(bundle, "hourlybaseddata")["series"]:
        if series["name"] in ("Total", "Singara Chennai Card"):
            series["data"] = [total if i == 3 else 0 for i in range(len(series["data"]))]
    for line in payload(bundle, "stationData"):
        for series in line["series"]:
            if series["name"] in ("Total", "Singara Chennai Card"):
                series["data"] = [per_station] * len(line["categories"])
    return bundle


def log(tmp_path):
    return tmp_path / "output/discrepancies/anomaly_log.jsonl"


def test_identical_replay_is_a_noop(snapshot_bundle, collect_snapshot, tmp_path):
    assert collect_snapshot(snapshot_bundle).exit_code == 0
    before = {**csv_bytes(tmp_path), log(tmp_path): log(tmp_path).read_bytes()}
    evidence = set((tmp_path / "output").rglob("*.json"))
    result = collect_snapshot(snapshot_bundle)
    assert result.exit_code == 0 and not result.published
    assert {path: path.read_bytes() for path in before} == before
    assert set((tmp_path / "output").rglob("*.json")) == evidence


@pytest.mark.parametrize("count", (0, 4, 6))
def test_valid_revisions_replace_the_entire_day_and_preserve_history(count, snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    originals = frames(tmp_path)
    for filename, frame in zip(ridership.FILENAMES, originals):
        history = pd.concat([frame.assign(Date="2000-01-01"), frame, frame.assign(Date="2099-12-31")], ignore_index=True)
        history.to_csv(tmp_path / "output" / filename, index=False)
    result = collect_snapshot(changed_counts(snapshot_bundle, count))
    assert result.exit_code == 0
    for actual, old in zip(frames(tmp_path), originals):
        current = actual.loc[actual.Date == "2026-11-06"]
        assert pd.to_numeric(current.Total).sum() == 54 * count
        for date in ("2000-01-01", "2099-12-31"):
            assert_frame_equal(actual.loc[actual.Date == date].reset_index(drop=True), old.assign(Date=date))
    record = read_last_record(log(tmp_path))
    assert record["status"] == "published_final" and record["validation_passed"]
    assert bool(record["anomalies"]) == (count != 5)
    if count < 5:
        assert len(record["anomalies"]) == 56
        assert len(result.raw_paths) == 1
        replay = ridership.collect_ridership(api_response=tmp_path / "output" / result.raw_paths[0],
                                             output_dir=tmp_path / "replay")
        assert replay.exit_code == 0
        assert pd.read_csv(tmp_path / "replay" / ridership.DAILY_FILENAME).Total.iloc[0] == 54 * count
    else:
        assert len(result.raw_paths) == 1


def test_failed_final_does_not_accept_individual_good_rows(snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    before = csv_bytes(tmp_path)
    incoming = changed_counts(snapshot_bundle, 6)
    payload(incoming, "allTicketCount")["totalTickets"] += 1
    result = collect_snapshot(incoming)
    assert result.status == "needs_review"
    assert not result.published
    assert csv_bytes(tmp_path) == before


def test_valid_mixed_fare_changes_are_accepted_together(snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    bundle = copy.deepcopy(snapshot_bundle)
    bundle["recorded_at_utc"] = "2026-11-07T05:00:00+00:00"
    payload(bundle, "allTicketCount").update(noOfNCMCcard=269, noOfPaperQR=1, noOfTotal_QR=1)
    hourly = payload(bundle, "hourlybaseddata")
    for series in hourly["series"]:
        if series["name"] == "Singara Chennai Card":
            series["data"][3] -= 1
        elif series["name"] == "Paper QR":
            series["data"][3] += 1
    for series in payload(bundle, "stationData")[0]["series"]:
        if series["name"] == "Singara Chennai Card":
            series["data"][0] -= 1
        elif series["name"] == "Paper QR":
            series["data"][0] += 1
    result = collect_snapshot(bundle)
    assert result.exit_code == 0 and len(result.revisions) == 3
    assert [pd.to_numeric(frame.Total).sum() for frame in frames(tmp_path)] == [270, 270, 270]
    assert frames(tmp_path)[0].iloc[0]["noOfPaperQR"] == "1"


def test_final_precedence_and_old_capture_protection(snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    before = csv_bytes(tmp_path)
    assert collect_snapshot(live(snapshot_bundle), day="0").status == "unchanged"
    older = changed_counts(snapshot_bundle, 4)
    older["recorded_at_utc"] = "2026-11-07T03:00:00+00:00"
    assert collect_snapshot(older).status == "unchanged"
    assert csv_bytes(tmp_path) == before


def test_fresh_unchanged_capture_advances_the_watermark(snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    before = csv_bytes(tmp_path)
    snapshot_bundle["recorded_at_utc"] = "2026-11-07T06:00:00+00:00"
    assert collect_snapshot(snapshot_bundle).exit_code == 0
    assert csv_bytes(tmp_path) == before
    older = changed_counts(snapshot_bundle, 4)  # 05:00 capture
    assert collect_snapshot(older).status == "unchanged"
    assert csv_bytes(tmp_path) == before
    assert len(log(tmp_path).read_text().splitlines()) == 2


def test_final_promotes_live_snapshot_without_rewriting_csvs(snapshot_bundle, collect_snapshot, tmp_path):
    assert collect_snapshot(live(snapshot_bundle), day="0").status == "published_provisional"
    before = csv_bytes(tmp_path)
    result = collect_snapshot(snapshot_bundle)
    assert result.status == "published_final" and result.exit_code == 0
    assert csv_bytes(tmp_path) == before
    record = read_last_record(log(tmp_path))
    assert record["compared_snapshot"]["day"] == "0"
    assert len(result.raw_paths) == 1


@pytest.mark.parametrize("failure", ("arithmetic", "structure"))
def test_held_final_preserves_the_live_values_it_was_compared_to(
        failure, snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(live(snapshot_bundle), day="0")
    before = csv_bytes(tmp_path)
    if failure == "arithmetic":
        payload(snapshot_bundle, "allTicketCount")["totalTickets"] += 1
    else:
        payload(snapshot_bundle, "stationData").pop()
    result = collect_snapshot(snapshot_bundle)
    assert result.exit_code == 2 and not result.published
    assert csv_bytes(tmp_path) == before
    baseline = read_last_record(log(tmp_path))["compared_snapshot"]
    assert baseline["day"] == "0" and baseline["status"] == "published_provisional"
    assert baseline["captured_at_utc"] == "2026-11-06T12:00:00+00:00"
    for name, frame in zip((s.name for s in ridership.SCHEMAS), frames(tmp_path)):
        assert baseline["rows"][name] == frame.to_dict("records")


def test_final_correction_does_not_mislabel_an_earlier_final_as_live(snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    collect_snapshot(changed_counts(snapshot_bundle, 4))
    baseline = read_last_record(log(tmp_path))["compared_snapshot"]
    assert baseline["day"] == "1" and baseline["status"] == "published_final"
    assert baseline["rows"]["ridership_daily"][0]["Total"] == "270"


def test_legacy_archive_baseline_is_not_guessed_to_be_live(snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    log(tmp_path).unlink()
    collect_snapshot(changed_counts(snapshot_bundle, 4))
    baseline = read_last_record(log(tmp_path))["compared_snapshot"]
    assert baseline["day"] is None and baseline["captured_at_utc"] is None
    assert baseline["rows"]["ridership_daily"][0]["Total"] == "270"


@pytest.mark.parametrize("date,prior_count,total", (
    ("2026-11-09", 1, 230346), ("2026-11-10", 5, 342598),
))
def test_captured_final_responses_can_remove_a_previously_nonzero_hour(
        date, prior_count, total, collect_snapshot, snapshot_clock, tmp_path):
    fixture = Path(__file__).parent / "fixtures/api" / f"final_{date}.json"
    final = json.loads(fixture.read_text(encoding="utf-8"))
    initial = live(final, date + "T12:00:00+00:00")
    hourly = payload(initial, "hourlybaseddata")
    six = next(i for i, value in enumerate(hourly["categories"]) if value[11:16] == "06:00")
    hourly["categories"].append(date + "T01:00:00.000Z")
    for series in hourly["series"]:
        active = series["name"] in ("Total", "Singara Chennai Card")
        series["data"].append(prior_count if active else 0)
        if active:
            series["data"][six] -= prior_count
    # This derived live baseline is synthetic; the final fixture stays unchanged.
    initial.pop("response_timestamps")
    initial["finished_at_utc"] = initial["recorded_at_utc"]
    snapshot_clock.now.return_value = datetime.fromisoformat(initial["recorded_at_utc"])
    assert collect_snapshot(initial, day="0").exit_code == 0
    snapshot_clock.now.return_value = datetime.fromisoformat(final["recorded_at_utc"])
    result = collect_snapshot(final)
    assert result.exit_code == 0 and result.status == "published_final"
    actual = frames(tmp_path)
    assert [pd.to_numeric(frame.Total).sum() for frame in actual] == [total, total, total]
    assert "01:00" not in set(actual[1].Hour)
    assert any(r["action"] == "removed" and r["key"]["Hour"] == "01:00" for r in result.revisions)
    record = read_last_record(log(tmp_path))
    prior = record["compared_snapshot"]["rows"]["ridership_hourly"]
    assert next(row for row in prior if row["Hour"] == "01:00")["Total"] == str(prior_count)
    before = {**csv_bytes(tmp_path), log(tmp_path): log(tmp_path).read_bytes()}
    assert not collect_snapshot(final).published
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("valid", (True, False))
def test_dry_run_is_write_free_and_does_not_sleep(valid, snapshot_bundle, collect_snapshot, tmp_path):
    if not valid:
        payload(snapshot_bundle, "allTicketCount")["totalTickets"] += 1
    result = collect_snapshot(snapshot_bundle, dry_run=True)
    assert result.exit_code == (0 if valid else 2)
    assert not result.published
    assert not (tmp_path / "output").exists()
    ridership.sleep.assert_not_called()


def test_latest_complete_attempt_is_used_when_retry_is_incomplete(snapshot_bundle, snapshot_clock, tmp_path):
    snapshot_clock.now.return_value = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    first = live(snapshot_bundle)
    payload(first, "allTicketCount")["totalTickets"] = 271
    second = copy.deepcopy(first)
    payload(second, "stationData").pop()
    with responses.RequestsMock() as mock:
        for bundle in (first, second):
            for url, data in bundle["responses"].items():
                mock.get(url, json=data)
        result = ridership.collect_ridership(day="0", output_dir=tmp_path / "output")
    assert result.exit_code == 2 and result.published
    assert read_last_record(log(tmp_path))["selected_attempt"] == 1
    assert [pd.to_numeric(frame.Total).sum() for frame in frames(tmp_path)] == [271, 270, 270]


def test_no_retry_across_midnight(snapshot_bundle, snapshot_clock, tmp_path, monkeypatch):
    snapshot_clock.now.return_value = datetime(2026, 11, 6, 18, 17, tzinfo=timezone.utc)
    bundle = live(snapshot_bundle)
    payload(bundle, "stationData").pop()
    def jump(_):
        snapshot_clock.now.return_value = datetime(2026, 11, 6, 18, 31, tzinfo=timezone.utc)
    monkeypatch.setattr(ridership, "sleep", jump)
    with responses.RequestsMock() as mock:
        for url, data in bundle["responses"].items():
            mock.get(url, json=data)
        result = ridership.collect_ridership(day="0", output_dir=tmp_path / "output")
        assert len(mock.calls) == 3
    assert result.status == "pending_retry"
    assert not csv_bytes(tmp_path)


def test_cutoff_uses_fallback_without_an_unsafe_retry(snapshot_bundle, collect_snapshot, tmp_path):
    bundle = live(snapshot_bundle, "2026-11-06T18:19:30+00:00")  # 23:49:30 IST
    payload(bundle, "allTicketCount")["totalTickets"] += 1
    result = collect_snapshot(bundle, day="0")
    assert result.exit_code == 2 and result.published
    ridership.sleep.assert_not_called()
    assert len(result.raw_paths) == 1


@pytest.mark.parametrize("phase", ("serialize", "replace"))
def test_group_write_failure_restores_original_snapshot(phase, snapshot_bundle, collect_snapshot, tmp_path, monkeypatch):
    collect_snapshot(snapshot_bundle)
    before = {**csv_bytes(tmp_path), log(tmp_path): log(tmp_path).read_bytes()}
    if phase == "serialize":
        original = pd.DataFrame.to_csv
        calls = []
        def fail(frame, *args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("disk error")
            return original(frame, *args, **kwargs)
        monkeypatch.setattr(pd.DataFrame, "to_csv", fail)
    else:
        original = Path.replace
        calls = []
        def fail(path, target):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("disk error")
            return original(path, target)
        monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="disk error"):
        collect_snapshot(changed_counts(snapshot_bundle, 4))
    assert {path: path.read_bytes() for path in before} == before
    assert not list((tmp_path / "output").rglob("*.tmp"))


def test_publication_log_failure_restores_the_csv_snapshot(snapshot_bundle, collect_snapshot, tmp_path, monkeypatch):
    collect_snapshot(snapshot_bundle)
    before = {**csv_bytes(tmp_path), log(tmp_path): log(tmp_path).read_bytes()}
    def fail(*args, **kwargs):
        raise OSError("log write failed")
    monkeypatch.setattr(ridership, "append_jsonl", fail)
    with pytest.raises(OSError, match="log write failed"):
        collect_snapshot(changed_counts(snapshot_bundle, 4))
    assert {path: path.read_bytes() for path in before} == before


def test_group_write_rolls_back_new_files(tmp_path, monkeypatch):
    original = Path.replace
    calls = []
    def fail(path, target):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk error")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        write_csv_group([(tmp_path / "a.csv", pd.DataFrame([{"Total": 1}])),
                         (tmp_path / "b.csv", pd.DataFrame([{"Total": 1}]))])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("finalized", (False, True))
def test_legacy_pending_retry_recovers_or_recognizes_final_data(finalized, snapshot_bundle, snapshot_clock, tmp_path):
    output = tmp_path / "output"
    previous = {"source": "ridership", "day": "0", "date": "2026-11-06", "status": "pending_retry",
                "utc_timestamp": "2026-11-06T11:40:00Z", "reason": "strict_upsert_anomaly", "anomalies": []}
    append_jsonl(log(tmp_path), previous)
    path = tmp_path / "input.json"
    if finalized:
        path.write_text(json.dumps(snapshot_bundle))
        ridership.collect_ridership(api_response=path, output_dir=output)
        # Put another old pending record last, as a delayed legacy workflow might.
        append_jsonl(log(tmp_path), previous)
    else:
        snapshot_clock.now.return_value = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        path.write_text(json.dumps(live(snapshot_bundle)))
    args = SimpleNamespace(output_dir=output, anomaly_log=ridership.ANOMALY_LOG_PATH,
                           dry_run=False, api_response=path)
    assert ridership.retry_ridership(args) == 0
    assert read_last_record(log(tmp_path))["status"] == "resolved"
    assert len(csv_bytes(tmp_path)) == 3


@pytest.mark.parametrize("status,recorded,dry_run,expected", (
    ("published_provisional", "2026-11-06T12:00:00+00:00", False, 0),
    ("pending_retry", "2026-11-06T11:45:00+00:00", False, 0),
    ("pending_retry", "2026-11-06T18:20:00+00:00", False, 2),
    ("pending_retry", "2026-11-07T04:00:00+00:00", False, 2),
    ("pending_retry", "2026-11-06T18:20:00+00:00", True, 2),
))
def test_retry_uses_recorded_clock_and_does_not_query_wrong_day(
        status, recorded, dry_run, expected, snapshot_bundle, tmp_path, monkeypatch):
    previous = {"source": "ridership", "day": "0", "date": "2026-11-06", "status": status,
                "utc_timestamp": "2026-11-06T11:40:00Z"}
    append_jsonl(log(tmp_path), previous)
    before = log(tmp_path).read_bytes()
    path = tmp_path / "input.json"
    path.write_text(json.dumps(live(snapshot_bundle, recorded)))
    def forbidden(*args, **kwargs):
        raise AssertionError("retry must not collect")
    monkeypatch.setattr(ridership, "collect_ridership", forbidden)
    args = SimpleNamespace(output_dir=tmp_path / "output", anomaly_log=ridership.ANOMALY_LOG_PATH,
                           dry_run=dry_run, api_response=path)
    assert ridership.retry_ridership(args) == expected
    if expected == 2 and not dry_run:
        assert read_last_record(log(tmp_path))["status"] == "expired"
    else:
        assert log(tmp_path).read_bytes() == before


def test_dry_run_validates_even_when_published_capture_is_newer(snapshot_bundle, collect_snapshot):
    collect_snapshot(snapshot_bundle)
    snapshot_bundle["recorded_at_utc"] = "2026-11-07T03:00:00+00:00"
    payload(snapshot_bundle, "allTicketCount")["totalTickets"] += 1
    assert collect_snapshot(snapshot_bundle, dry_run=True).exit_code == 2


def test_pending_date_mismatch_never_changes_published_csvs(snapshot_bundle, collect_snapshot, tmp_path):
    collect_snapshot(snapshot_bundle)
    before = csv_bytes(tmp_path)
    result = collect_snapshot(live(snapshot_bundle), day="0", expected_date="2026-11-05")
    assert result.exit_code == 2 and not result.published
    assert any(f["code"] == "selector_date_mismatch" for f in result.findings)
    assert csv_bytes(tmp_path) == before
    ridership.sleep.assert_not_called()


def test_retry_capture_cannot_move_behind_the_publication_watermark(
        snapshot_bundle, collect_snapshot, tmp_path, monkeypatch):
    collect_snapshot(snapshot_bundle)
    before = csv_bytes(tmp_path)
    newer_bad = changed_counts(snapshot_bundle, 6)
    payload(newer_bad, "allTicketCount")["totalTickets"] += 1
    older_good = changed_counts(snapshot_bundle, 4)
    older_good["recorded_at_utc"] = "2026-11-07T03:00:00+00:00"
    monkeypatch.setattr(ridership, "sleep", lambda _: (tmp_path / "input.json").write_text(json.dumps(older_good)))
    result = collect_snapshot(newer_bad)
    assert result.exit_code == 2 and not result.published
    assert any(f["code"] == "stale_capture" for f in result.findings)
    assert csv_bytes(tmp_path) == before
