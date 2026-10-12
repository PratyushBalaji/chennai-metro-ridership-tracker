from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collector.anomalies import append_jsonl, read_last_line, read_last_record, retry_decision


def test_anomaly_log_reads_last_non_empty_line(tmp_path):
    log_path = tmp_path / "anomaly_log.jsonl"
    first = {"status": "pending_retry", "date": "2026-04-30"}
    second = {"status": "resolved", "date": "2026-04-30"}

    append_jsonl(log_path, first)
    append_jsonl(log_path, second)

    assert read_last_record(log_path) == second
    assert read_last_line(log_path).endswith('"status": "resolved"}')


def test_missing_anomaly_log_has_no_last_line(tmp_path):
    assert read_last_line(tmp_path / "missing.jsonl") is None
    assert read_last_record(tmp_path / "missing.jsonl") is None


@pytest.mark.parametrize("status", ("resolved", "expired", "needs_review"))
def test_retry_ignores_terminal_records(status):
    assert retry_decision({"status": status}) == "skip"


@pytest.mark.parametrize("age,expected", ((0, "wait"), (14, "wait"), (15, "due"), (16, "due")))
def test_retry_waits_fifteen_minutes(age, expected):
    now = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
    record = {"status": "pending_retry", "source": "ridership", "day": "0", "date": "2026-06-15",
              "utc_timestamp": (now - timedelta(minutes=age)).isoformat()}
    assert retry_decision(record, now) == expected


@pytest.mark.parametrize("newline", ("\n", "\r\n"))
@pytest.mark.parametrize("suffix", ("", "\n", "\n\n \t\n"))
def test_tail_reads_complete_large_unicode_record(newline, suffix, tmp_path):
    record = {"status": "pending_retry", "details": "\u00e9\u0ba4" * 6000}
    last = json.dumps(record, ensure_ascii=False)
    path = tmp_path / "large.jsonl"
    path.write_bytes((json.dumps({"details": "x" * 10000}) + newline + last + suffix).encode("utf-8"))
    assert json.loads(read_last_line(path, chunk_size=31)) == record
    assert read_last_record(path) == record


def test_short_tail_does_not_load_large_log(tmp_path):
    path = tmp_path / "large.jsonl"
    path.write_text(json.dumps({"details": "x" * 100000}) + '\n{"status": "resolved"}\n', encoding="utf-8")
    with path.open("rb") as file:
        tracked = MagicMock(wraps=file)
        tracked.__enter__.return_value = tracked
        with patch.object(Path, "open", return_value=tracked):
            assert read_last_record(path) == {"status": "resolved"}
        assert sum(call.args[0] for call in tracked.read.call_args_list) < path.stat().st_size


@pytest.mark.parametrize("content", (b"", b" \n\r\n" * 3000))
def test_empty_or_blank_log_has_no_record(content, tmp_path):
    path = tmp_path / "blank.jsonl"
    path.write_bytes(content)
    assert read_last_record(path) is None


@pytest.mark.parametrize("now,date,started,expected", (
    ("2026-06-15T18:19:00+00:00", "2026-06-15", "2026-06-15T17:00:00Z", "due"),
    ("2026-06-15T18:20:00+00:00", "2026-06-15", "2026-06-15T17:00:00Z", "expired"),
    ("2026-06-15T18:35:00+00:00", "2026-06-15", "2026-06-15T17:00:00Z", "expired"),
    ("2026-06-16T04:00:00+00:00", "2026-06-15", "2026-06-15T17:00:00Z", "expired"),
    ("2026-06-16T12:00:00+00:00", "2026-06-15", "2026-06-16T11:30:00Z", "expired"),
    ("2026-06-15T12:00:00+00:00", "2026-06-15", "2026-06-15T12:01:00Z", "wait"),
    ("2026-06-15T12:00:00+00:00", "2026-06-16", "2026-06-15T11:30:00Z", "wait"),
))
def test_retry_cutoff_and_date_boundaries(now, date, started, expected):
    record = {"status": "pending_retry", "source": "ridership", "day": "0", "date": date, "utc_timestamp": started}
    assert retry_decision(record, datetime.fromisoformat(now)) == expected


@pytest.mark.parametrize("source,day", (("parking", "0"), ("ridership", "1")))
def test_retry_ignores_other_sources_and_static_data(source, day):
    assert retry_decision({"status": "pending_retry", "source": source, "day": day}) == "skip"
