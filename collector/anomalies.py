from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from collector.upsert import UpsertResult


ANOMALY_EXIT_CODE = 2
ANOMALY_LOG_PATH = Path("Discrepancies/anomaly_log.jsonl")
IST = ZoneInfo("Asia/Kolkata")


def has_anomalies(results: Iterable[UpsertResult]) -> bool:
    return any(result.anomalies for result in results)


def append_anomaly_record(
    path: str | Path,
    *,
    source: str,
    day: str,
    dataset_date: str,
    results: Iterable[UpsertResult],
    payload_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    anomalies = _result_anomalies(results)
    if not anomalies:
        return None

    status = "pending_retry" if day == "0" else "needs_review"
    record = build_log_record(
        source=source,
        day=day,
        dataset_date=dataset_date,
        status=status,
        reason="strict_upsert_anomaly",
        anomalies=anomalies,
        payload_summary=payload_summary or {},
    )
    append_jsonl(path, record)
    return record


def build_log_record(
    *,
    source: str,
    day: str,
    dataset_date: str,
    status: str,
    reason: str,
    anomalies: list[dict[str, Any]] | None = None,
    payload_summary: dict[str, Any] | None = None,
    previous_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)
    record: dict[str, Any] = {
        "utc_timestamp": now_utc.isoformat().replace("+00:00", "Z"),
        "ist_timestamp": now_ist.isoformat(),
        "source": source,
        "day": day,
        "date": dataset_date,
        "status": status,
        "reason": reason,
        "anomalies": anomalies or [],
        "payload_summary": payload_summary or {},
    }
    if previous_record:
        record["previous"] = {
            "utc_timestamp": previous_record.get("utc_timestamp"),
            "ist_timestamp": previous_record.get("ist_timestamp"),
            "status": previous_record.get("status"),
            "reason": previous_record.get("reason"),
        }
    return record


def append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(_json_value(record), ensure_ascii=True, sort_keys=True))
        file.write("\n")


def read_last_record(path: str | Path) -> dict[str, Any] | None:
    line = read_last_line(path)
    if not line:
        return None
    return json.loads(line)


def read_last_line(path: str | Path, chunk_size: int = 4096) -> str | None:
    log_path = Path(path)
    if not log_path.exists() or log_path.stat().st_size == 0:
        return None

    with log_path.open("rb") as file:
        file.seek(0, 2)
        position = file.tell()
        buffer = b""

        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            file.seek(position)
            buffer = file.read(read_size) + buffer
            lines = [line.strip() for line in buffer.splitlines() if line.strip()]
            if lines:
                return lines[-1].decode("utf-8")

    return None


def _result_anomalies(results: Iterable[UpsertResult]) -> list[dict[str, Any]]:
    anomalies = []
    for result in results:
        for decision in result.decisions:
            if not decision.conflicts:
                continue
            anomalies.append(
                {
                    "dataset": decision.dataset,
                    "key": decision.key,
                    "action": decision.action,
                    "details": [
                        {
                            "column": conflict.column,
                            "existing": conflict.existing,
                            "incoming": conflict.incoming,
                            "reason": conflict.reason,
                        }
                        for conflict in decision.conflicts
                    ],
                }
            )
    return anomalies


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
