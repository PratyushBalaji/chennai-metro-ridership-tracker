from __future__ import annotations

import argparse

from collector.anomalies import ANOMALY_LOG_PATH, append_jsonl, build_log_record, read_last_record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append a status row to the anomaly log.")
    parser.add_argument("--log", default=ANOMALY_LOG_PATH)
    parser.add_argument("--status", required=True, choices=("resolved", "expired"))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--source", default=None)
    parser.add_argument("--day", default=None)
    parser.add_argument("--date", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    previous = read_last_record(args.log)
    record = build_log_record(
        source=args.source or _previous_value(previous, "source", "ridership"),
        day=args.day or _previous_value(previous, "day", "0"),
        dataset_date=args.date or _previous_value(previous, "date", ""),
        status=args.status,
        reason=args.reason,
        previous_record=previous,
    )
    append_jsonl(args.log, record)
    print(f"logged {args.status}")


def _previous_value(record: dict | None, key: str, default: str) -> str:
    if not record:
        return default
    return str(record.get(key) or default)


if __name__ == "__main__":
    main()
