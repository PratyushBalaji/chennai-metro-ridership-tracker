from __future__ import annotations

import argparse
import os
from datetime import datetime, time, timezone
from pathlib import Path

from collector.anomalies import ANOMALY_LOG_PATH, IST, read_last_record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check if the latest anomaly is due for retry.")
    parser.add_argument("--log", default=ANOMALY_LOG_PATH, help="Append-only JSONL anomaly log")
    parser.add_argument("--wait-minutes", type=int, default=15)
    parser.add_argument("--cutoff-ist", default="23:50")
    parser.add_argument("--source", default="ridership")
    parser.add_argument("--day", default="0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    record = read_last_record(args.log)
    outputs = {
        "should_retry": "false",
        "expired": "false",
        "status": "none",
        "source": args.source,
        "day": args.day,
        "date": "",
        "age_minutes": "0",
    }

    if not record:
        write_outputs(outputs)
        print("no anomaly log")
        return

    outputs["status"] = str(record.get("status", ""))
    outputs["source"] = str(record.get("source", ""))
    outputs["day"] = str(record.get("day", ""))
    outputs["date"] = str(record.get("date", ""))

    if record.get("status") != "pending_retry":
        write_outputs(outputs)
        print("latest anomaly is not pending")
        return

    if record.get("source") != args.source or str(record.get("day")) != args.day:
        write_outputs(outputs)
        print("latest anomaly is not for this retry flow")
        return

    now_utc = datetime.now(timezone.utc)
    started_at = parse_utc(str(record["utc_timestamp"]))
    age_minutes = int((now_utc - started_at).total_seconds() // 60)
    outputs["age_minutes"] = str(age_minutes)

    cutoff = parse_cutoff(args.cutoff_ist)
    now_ist = now_utc.astimezone(IST).time().replace(tzinfo=None)
    if now_ist >= cutoff:
        outputs["expired"] = "true"
        write_outputs(outputs)
        print("pending retry is past cutoff")
        return

    if age_minutes >= args.wait_minutes:
        outputs["should_retry"] = "true"
        write_outputs(outputs)
        print("retry due")
        return

    write_outputs(outputs)
    print("retry not due yet")


def parse_utc(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def parse_cutoff(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def write_outputs(outputs: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        for key, value in outputs.items():
            print(f"{key}={value}")
        return

    with Path(output_path).open("a", encoding="utf-8") as file:
        for key, value in outputs.items():
            file.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
