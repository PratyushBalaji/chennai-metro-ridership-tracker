import argparse
from pathlib import Path

import pandas as pd

from collector import fetch_json
from collector.anomalies import ANOMALY_EXIT_CODE, ANOMALY_LOG_PATH, append_anomaly_record, has_anomalies
from collector.schemas import PHPDT_DAILY
from collector.upsert import upsert_csv


DAILY_FILENAME = "PHPDT/ChennaiMetro_Daily_PHPDT.csv"
PHPDT_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/PHPDTreport/"
ROUTE_MAPPING = {
    "saPtoSWDViewModel": {"line": "1", "direction": "UP"},
    "swDtoSAPViewModel": {"line": "1", "direction": "DOWN"},
    "smMtoSCCViewModel": {"line": "2", "direction": "UP"},
    "scCtoSMMViewModel": {"line": "2", "direction": "DOWN"},
}


def extract_station_code(corridor):
    """Extract station codes from a route key like 'seG_SCC2'."""
    parts = "".join(char for char in corridor if char.isalpha() or char == "_").split("_")
    if len(parts) == 2:
        return parts[0].upper(), parts[1].upper()
    return None, None


def normalize_phpdt(payload):
    rows = []
    for route_key, route in ROUTE_MAPPING.items():
        if not payload.get(route_key):
            print(f"Warning: No data available for route key '{route_key}'")
            continue
        for entry in payload[route_key]:
            start = pd.to_datetime(entry["transfromdate"])
            end = pd.to_datetime(entry["transtodate"])
            for corridor, count in entry.items():
                if "_" not in corridor:
                    continue
                start_station, end_station = extract_station_code(corridor)
                if not start_station or not end_station:
                    print(f"Error: Could not extract stations from corridor key '{corridor}'")
                    continue
                rows.append({
                    "Date": start.strftime("%Y-%m-%d"),
                    "Line": route["line"],
                    "Direction": route["direction"],
                    "Start Hour": start.strftime("%H:%M"),
                    "End Hour": end.strftime("%H:%M"),
                    "Start Station": start_station,
                    "End Station": end_station,
                    "PHPDT": count,
                })
    return pd.DataFrame(rows)


def collect_phpdt(output_dir=".", api_response=None, anomaly_log=ANOMALY_LOG_PATH):
    dataframe = normalize_phpdt(fetch_json(PHPDT_URL, api_response))
    if dataframe.empty:
        print("No PHPDT data available to process.")
        return []
    result = upsert_csv(Path(output_dir) / DAILY_FILENAME, dataframe, PHPDT_DAILY, allow_increases=False)
    print(f"phpdt_daily: inserted={result.inserted}, updated={result.updated}, anomalies={result.anomalies}")
    results = [result]
    append_anomaly_record(
        Path(output_dir) / anomaly_log, source="phpdt", day="1",
        dataset_date=dataframe.iloc[0]["Date"], results=results,
    )
    return results


def main():
    parser = argparse.ArgumentParser(description="Collect Chennai Metro PHPDT data.")
    parser.add_argument("--api-response", type=Path, help="Read a JSON response bundle instead of calling the API")
    parser.add_argument("--output-dir", default=".", help="Directory containing the PHPDT output folder")
    parser.add_argument("--anomaly-log", type=Path, default=ANOMALY_LOG_PATH)
    args = parser.parse_args()
    results = collect_phpdt(args.output_dir, args.api_response, args.anomaly_log)
    if has_anomalies(results):
        raise SystemExit(ANOMALY_EXIT_CODE)


if __name__ == "__main__":
    main()
