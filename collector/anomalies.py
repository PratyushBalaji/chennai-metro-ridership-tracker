from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from collector.upsert import UpsertResult


ANOMALY_EXIT_CODE = 2
ANOMALY_LOG_PATH = Path("Discrepancies/anomaly_log.jsonl")
IST = ZoneInfo("Asia/Kolkata")
RETRY_WAIT_MINUTES = 15
RETRY_CUTOFF_IST = time(23, 50)


def retry_decision(record: dict[str, Any] | None, now: datetime | None = None) -> str:
    if not record or record.get("status") != "pending_retry":
        return "skip"
    if record.get("source") != "ridership" or str(record.get("day")) != "0":
        return "skip"
    now = now or datetime.now(timezone.utc)
    started_at = datetime.fromisoformat(record["utc_timestamp"].replace("Z", "+00:00"))
    now_ist = now.astimezone(IST)
    dataset_date = date.fromisoformat(record["date"])
    if (started_at.astimezone(IST).date() < now_ist.date() or dataset_date < now_ist.date()
            or now_ist.time().replace(tzinfo=None) >= RETRY_CUTOFF_IST):
        return "expired"
    if dataset_date > now_ist.date():
        return "wait"
    if (now - started_at).total_seconds() >= RETRY_WAIT_MINUTES * 60:
        return "due"
    return "wait"


def append_retry_status(path: str | Path, previous: dict[str, Any], status: str, reason: str) -> None:
    append_jsonl(path, build_log_record(
        source=previous["source"], day=previous["day"], dataset_date=previous["date"],
        status=status, reason=reason, previous_record=previous,
    ))


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
            content = buffer.rstrip()
            if not content:
                continue
            line_start = content.rfind(b"\n")
            if line_start >= 0 or position == 0:
                return content[line_start + 1:].strip().decode("utf-8")

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
