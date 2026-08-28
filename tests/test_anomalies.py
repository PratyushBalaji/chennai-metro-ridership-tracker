from collector.anomalies import append_jsonl, read_last_line, read_last_record


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
