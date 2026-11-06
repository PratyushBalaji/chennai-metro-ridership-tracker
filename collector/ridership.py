from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from collector import fetch_json

from collector.anomalies import (
    ANOMALY_EXIT_CODE, ANOMALY_LOG_PATH, append_anomaly_record,
    append_jsonl, append_retry_status, build_log_record, has_anomalies,
    read_last_record, retry_decision,
)
from collector.schemas import RIDERSHIP_DAILY, RIDERSHIP_HOURLY, RIDERSHIP_STATION
from collector.upsert import UpsertResult, read_csv, upsert_rows, write_csv_atomic


DAILY_FILENAME = "ridership/ChennaiMetro_Daily_Ridership.csv"
HOURLY_FILENAME = "ridership/ChennaiMetro_Hourly_Ridership.csv"
STATION_FILENAME = "ridership/ChennaiMetro_Station_Ridership.csv"

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"


class PayloadUnavailableError(ValueError):
    pass

STATION_CODES = {
    # Blue Line
    "WIMCO NAGAR DEPOT": "SWD",
    "WIMCO NAGAR METRO": "SWN",
    "THIRUVOTRIYUR METRO": "STV",
    "THIRUVOTRIYUR THERADI METRO": "STT",
    "KALADIPET METRO": "SKP",
    "TOLLGATE METRO": "STG",
    "NEW WASHERMENPET METRO": "SNW",
    "TONDIARPET METRO": "STR",
    "THIYAGARAYA COLLEGE METRO": "STC",
    "WASHERMANPET": "SWA",
    "MANNADI": "SMA",
    "HIGH COURT": "SHC",
    "GOVERNMENT ESTATE": "SGE",
    "LIC": "SLI",
    "THOUSAND LIGHT": "STL",
    "AG-DMS": "SGM",
    "TEYNAMPET": "STE",
    "NANDANAM": "SCR",
    "SAIDAPET": "SSA",
    "LITTLE MOUNT": "SLM",
    "GUINDY": "SGU",
    "OTA - NANGANALLUR ROAD": "SOT",
    "MEENAMBAKKAM": "SME",
    "CHENNAI AIRPORT": "SAP",
    # Green Line
    "EGMORE": "SEG",
    "NEHRU PARK": "SNP",
    "KILPAUK": "SKM",
    "PACHAIAPPA S COLLEGE": "SPC",
    "SHENOY NAGAR": "SSN",
    "ANNA NAGAR EAST": "SAE",
    "ANNA NAGAR TOWER": "SAT",
    "THIRUMANGALAM": "STI",
    "KOYAMBEDU": "SKO",
    "CMBT": "SCM",
    "ARUMBAKKAM": "SAR",
    "VADAPALANI": "SVA",
    "ASHOK NAGAR": "SAN",
    "EKKATTUTHANGAL": "SSI",
    "St. THOMAS MOUNT": "SMM",
    # Interchange stations
    "CENTRAL  METRO": "SCC",
    "ALANDUR": "SAL",
    # Yellow Line (corridor 4)
    "POONAMALLEE BYPASS METRO": "POB",
    "POONAMALLEE BUS STAND METRO": "POO",
    "POONAMALLEE GOVERNMENT HOSPITAL METRO": "MUL",
    "KARAYANCHAVADI METRO": "KAC",
    "KUMANANCHAVADI METRO": "KUC",
    "KATTUPAKKAM METRO": "KPM",
    "IYAPPANTHANGAL METRO": "IYP",
    "THELLIYARAGARAM METRO": "THL",
    "PORUR BYPASS METRO": "PBP",
    "PORUR JUNCTION METRO": "PRJ",
}


def endpoint(path: str, day: str) -> str:
    return f"{BASE_URL}{path}/{day}"


def convert_station_code(station_name: str) -> str:
    return STATION_CODES.get(station_name, station_name)


def fetch_ridership_payloads(day: str, api_response: str | Path | None = None) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    daily = fetch_json(endpoint("allTicketCount", day), api_response)
    hourly = fetch_json(endpoint("hourlybaseddata", day), api_response)
    station = fetch_json(endpoint("stationData", day), api_response)
    return daily, hourly, station


def extract_dataset_date(hourly_response: dict[str, Any]) -> str:
    categories = hourly_response.get("categories") if isinstance(hourly_response, dict) else None
    if not categories or not categories[0]:
        raise PayloadUnavailableError("Hourly ridership response has no timestamp categories")
    return pd.to_datetime(categories[0]).strftime("%Y-%m-%d")


def normalize_daily(daily_tickets: dict[str, Any], date: str) -> pd.DataFrame:
    payment_methods = sorted(key for key in daily_tickets if key.startswith("noOf"))
    row = {
        "Date": date,
        "Total": daily_tickets["totalTickets"],
    }
    row.update({method: daily_tickets[method] for method in payment_methods})
    return pd.DataFrame([row], columns=["Date", "Total", *payment_methods])


def normalize_hourly(hourly_response: dict[str, Any]) -> pd.DataFrame:
    date = extract_dataset_date(hourly_response)
    timestamps = [pd.to_datetime(timestamp).strftime("%H:%M") for timestamp in hourly_response["categories"]]
    series = {entry["name"]: entry["data"] for entry in hourly_response["series"]}
    payment_methods = sorted(method for method in series if method != "Total")

    rows = []
    for index, timestamp in enumerate(timestamps):
        row = {
            "Date": date,
            "Hour": timestamp,
            "Total": series["Total"][index],
        }
        row.update({method: series[method][index] for method in payment_methods})
        rows.append(row)

    return pd.DataFrame(rows, columns=["Date", "Hour", "Total", *payment_methods])


def normalize_station(station_response: list[dict[str, Any]], date: str) -> pd.DataFrame:
    rows = []
    payment_methods: set[str] = set()

    for line_data in station_response:
        series = {entry["name"]: entry["data"] for entry in line_data["series"]}
        line_payment_methods = sorted(method for method in series if method != "Total")
        payment_methods.update(line_payment_methods)

        for index, station_name in enumerate(line_data["categories"]):
            row = {
                "Date": date,
                "Line": line_data["line"],
                "Station": convert_station_code(station_name),
                "Total": series["Total"][index],
            }
            row.update({method: series[method][index] for method in line_payment_methods})
            rows.append(row)

    return pd.DataFrame(rows, columns=["Date", "Line", "Station", "Total", *sorted(payment_methods)], dtype=object)


def collect_ridership(
    day: str = "1",
    output_dir: str | Path = "data",
    dry_run: bool = False,
    anomaly_log: str | Path = ANOMALY_LOG_PATH,
    api_response: str | Path | None = None,
    expected_date: str | None = None,
) -> list[UpsertResult]:
    daily_payload, hourly_payload, station_payload = fetch_ridership_payloads(day, api_response)
    try:
        date = extract_dataset_date(hourly_payload)
    except PayloadUnavailableError:
        date = None
    problem = None
    if date is None:
        problem = "payload_missing_hourly_categories"
    elif expected_date is not None and date != expected_date:
        problem = "payload_date_mismatch"
    if problem:
        if not dry_run:
            summary = summarize_payloads(daily_payload, hourly_payload, station_payload)
            summary["returned_dataset_date"] = date
            summary["date_inferred"] = expected_date is None
            record = build_log_record(
                source="ridership", day=day, dataset_date=expected_date or "",
                status="pending_retry" if day == "0" else "needs_review",
                reason=problem, payload_summary=summary,
            )
            if expected_date is None:
                ist_date = datetime.fromisoformat(record["ist_timestamp"]).date()
                record["date"] = (ist_date - timedelta(days=int(day))).isoformat()
            append_jsonl(_resolve_output_path(output_dir, anomaly_log), record)
        raise PayloadUnavailableError(problem)

    datasets = [
        (Path(output_dir) / DAILY_FILENAME, normalize_daily(daily_payload, date), RIDERSHIP_DAILY),
        (Path(output_dir) / HOURLY_FILENAME, normalize_hourly(hourly_payload), RIDERSHIP_HOURLY),
        (Path(output_dir) / STATION_FILENAME, normalize_station(station_payload, date), RIDERSHIP_STATION),
    ]

    results: list[UpsertResult] = []
    for path, dataframe, schema in datasets:
        if dry_run:
            print(f"Dry run: prepared {len(dataframe)} {schema.name} rows for {date}")
            continue

        column = "noOfUTPQR" if schema == RIDERSHIP_DAILY else "UTP QR"
        result = upsert_rows(read_csv(path), dataframe, schema, zero_columns=(column,))
        results.append(result)
        print(_format_result(schema.name, date, result))

    # Validate every dataset before writing any of them.
    for (path, _, _), result in zip(datasets, results):
        write_csv_atomic(path, result.dataframe)

    if results and has_anomalies(results):
        log_path = _resolve_output_path(output_dir, anomaly_log)
        append_anomaly_record(
            log_path,
            source="ridership",
            day=day,
            dataset_date=date,
            results=results,
            payload_summary=summarize_payloads(daily_payload, hourly_payload, station_payload),
        )
        print(f"anomaly log: {log_path}")

    return results


def summarize_payloads(
    daily_payload: dict[str, Any],
    hourly_payload: dict[str, Any],
    station_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    categories = (hourly_payload.get("categories") or []) if isinstance(hourly_payload, dict) else []
    return {
        "daily_total": daily_payload.get("totalTickets"),
        "daily_fields": len(daily_payload),
        "hourly_points": len(categories),
        "hourly_start": categories[0] if categories else None,
        "hourly_end": categories[-1] if categories else None,
        "station_lines": len(station_payload),
        "station_count": sum(len(line.get("categories") or []) for line in station_payload),
    }


def _resolve_output_path(output_dir: str | Path, path: str | Path) -> Path:
    log_path = Path(path)
    if log_path.is_absolute():
        return log_path
    return Path(output_dir) / log_path


def _format_result(dataset_name: str, date: str, result: UpsertResult) -> str:
    return (
        f"{dataset_name} {date}: "
        f"{result.inserted} inserted, {result.updated} updated, "
        f"{result.anomalies} anomalies, {result.conflicts} conflicts"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Chennai Metro ridership data.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--day", default="1", choices=("0", "1"), help="CMRL day selector: 0=today, 1=yesterday")
    mode.add_argument("--retry", action="store_true", help="Retry the latest pending live-data anomaly when due")
    parser.add_argument("--output-dir", default="data", help="Data root containing the ridership output folder")
    parser.add_argument("--api-response", type=Path, help="Read a JSON response bundle instead of calling the API")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and normalize without writing CSV files")
    parser.add_argument("--strict", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--anomaly-log", default=ANOMALY_LOG_PATH, help="Append-only JSONL anomaly log path")
    return parser.parse_args()


def retry_ridership(args: argparse.Namespace) -> int:
    log_path = _resolve_output_path(args.output_dir, args.anomaly_log)
    # Other collectors and /1 reviews must not hide the latest live retry state.
    previous = read_last_record(log_path, source="ridership", day="0")
    decision = retry_decision(previous)
    print(f"retry: {decision}")
    if decision in ("skip", "wait"):
        return 0
    if decision == "expired":
        if not args.dry_run:
            append_retry_status(log_path, previous, "expired", "retry_cutoff")
        return ANOMALY_EXIT_CODE
    results = collect_ridership(
        day="0", output_dir=args.output_dir, dry_run=args.dry_run,
        anomaly_log=args.anomaly_log, api_response=args.api_response,
        expected_date=previous["date"],
    )
    if has_anomalies(results):
        return ANOMALY_EXIT_CODE
    if not args.dry_run:
        append_retry_status(log_path, previous, "resolved", "retry_ok")
    return 0


def main() -> None:
    args = parse_args()
    try:
        if args.retry:
            code = retry_ridership(args)
        else:
            results = collect_ridership(
                day=args.day, output_dir=args.output_dir, dry_run=args.dry_run,
                anomaly_log=args.anomaly_log, api_response=args.api_response,
            )
            code = ANOMALY_EXIT_CODE if has_anomalies(results) else 0
    except PayloadUnavailableError as error:
        print(f"ridership: {error}")
        code = ANOMALY_EXIT_CODE
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
