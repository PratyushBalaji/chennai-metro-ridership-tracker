import argparse
from pathlib import Path

import pandas as pd

from collector import fetch_json
from collector.upsert import read_csv


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


def collect_phpdt(output_dir=".", api_response=None):
    dataframe = normalize_phpdt(fetch_json(PHPDT_URL, api_response))
    if dataframe.empty:
        print("No PHPDT data available to process.")
        return []
    target = Path(output_dir) / DAILY_FILENAME
    existing = read_csv(target)
    dataset_date = dataframe.iloc[0]["Date"]
    if not existing.empty and existing.iloc[-1]["Date"] == dataset_date:
        print(f"PHPDT data for {dataset_date} already exists. Skipping.")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_csv(target, index=False, mode="w" if existing.empty else "a", header=existing.empty)


def main():
    parser = argparse.ArgumentParser(description="Collect Chennai Metro PHPDT data.")
    parser.add_argument("--api-response", type=Path, help="Read a JSON response bundle instead of calling the API")
    parser.add_argument("--output-dir", default=".", help="Directory containing the PHPDT output folder")
    args = parser.parse_args()
    collect_phpdt(args.output_dir, args.api_response)


if __name__ == "__main__":
    main()
