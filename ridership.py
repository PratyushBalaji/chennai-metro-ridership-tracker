from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from collector.anomalies import ANOMALY_EXIT_CODE, ANOMALY_LOG_PATH, append_anomaly_record, has_anomalies
from collector.schemas import RIDERSHIP_DAILY, RIDERSHIP_HOURLY, RIDERSHIP_STATION
from collector.upsert import UpsertResult, upsert_csv


DAILY_FILENAME = "Ridership/ChennaiMetro_Daily_Ridership.csv"
HOURLY_FILENAME = "Ridership/ChennaiMetro_Hourly_Ridership.csv"
STATION_FILENAME = "Ridership/ChennaiMetro_Station_Ridership.csv"

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
REQUEST_TIMEOUT_SECONDS = 30

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
}


def endpoint(path: str, day: str) -> str:
    return f"{BASE_URL}{path}/{day}"


def convert_station_code(station_name: str) -> str:
    return STATION_CODES.get(station_name, station_name)


def fetch_json(url: str) -> Any:
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def fetch_ridership_payloads(day: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    daily = fetch_json(endpoint("allTicketCount", day))
    hourly = fetch_json(endpoint("hourlybaseddata", day))
    station = fetch_json(endpoint("stationData", day))
    return daily, hourly, station


def extract_dataset_date(hourly_response: dict[str, Any]) -> str:
    categories = hourly_response.get("categories") or []
    if not categories:
        raise ValueError("Hourly ridership response has no timestamp categories")
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

    return pd.DataFrame(rows, columns=["Date", "Line", "Station", "Total", *sorted(payment_methods)])


def collect_ridership(
    day: str = "1",
    output_dir: str | Path = ".",
    dry_run: bool = False,
    anomaly_log: str | Path = ANOMALY_LOG_PATH,
) -> list[UpsertResult]:
    daily_payload, hourly_payload, station_payload = fetch_ridership_payloads(day)
    date = extract_dataset_date(hourly_payload)

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

        result = upsert_csv(path, dataframe, schema)
        results.append(result)
        print(_format_result(schema.name, date, result))

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
    categories = hourly_payload.get("categories") or []
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
    parser.add_argument("--day", default="1", choices=("0", "1"), help="CMRL day selector: 0=today, 1=yesterday")
    parser.add_argument("--output-dir", default=".", help="Directory containing the Ridership output folder")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and normalize without writing CSV files")
    parser.add_argument("--strict", action="store_true", help="Use strict upsert checks")
    parser.add_argument("--anomaly-log", default=ANOMALY_LOG_PATH, help="Append-only JSONL anomaly log path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = collect_ridership(
        day=args.day,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        anomaly_log=args.anomaly_log,
    )
    if has_anomalies(results):
        raise SystemExit(ANOMALY_EXIT_CODE)


if __name__ == "__main__":
    main()
