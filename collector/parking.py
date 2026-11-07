import argparse
import os
from pathlib import Path

import pandas as pd

from collector import fetch_json
from collector.anomalies import ANOMALY_EXIT_CODE, ANOMALY_LOG_PATH, append_anomaly_record, has_anomalies
from collector.schemas import PARKING_DAILY, PARKING_HOURLY, PARKING_STATION
from collector.upsert import read_csv, upsert_rows, write_csv_atomic


DAILY_FILENAME = "parking/ChennaiMetro_Daily_Parking.csv"
HOURLY_FILENAME = "parking/ChennaiMetro_Hourly_Parking.csv"
STATION_FILENAME = "parking/ChennaiMetro_Station_Parking.csv"
BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/"
RIDERSHIP_HOURLY_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/hourlybaseddata/1"

PARKING_STATION_CODES = {
    # Blue Line
    "Wimco Nagar Depot Metro": "SWD",
    "Wimco Nagar Metro": "SWN",
    "Thiruvotriyur Metro": "STV",
    "Thiruvotriyur Theradi Metro": "STT",
    "Kaladipet Metro": "SKP",
    "Tollgate Metro": "STG",
    "New Washermenpet Metro": "SNW",
    "Tondiarpet Metro": "STR",
    "Thiagaraya College Metro": "STC",
    "Washermanpet": "SWA",
    "Mannadi": "SMA",
    "High Court": "SHC",
    "Government Estate": "SGE",
    "LIC": "SLI",
    "Thousand Lights": "STL",
    "AG-DMS": "SGM",
    "Teynampet": "STE",
    "Nandanam": "SCR",
    "Saidapet": "SSA",
    "Little Mount": "SLM",
    "Guindy": "SGU",
    "OTA - Nanganallur Road": "SOT",
    "Meenambakkam": "SME",
    "Chennai International Airport": "SAP",

    # Green Line
    "Puratchi Thalaivar Dr. M.G. Ramachandran Central": "SCC",
    "Egmore": "SEG",
    "Nehru Park": "SNP",
    "Kilpauk": "SKM",
    "Pachaiyappas College": "SPC",
    "Shenoy Nagar": "SSN",
    "Anna Nagar East": "SAE",
    "Anna Nagar Tower": "SAT",
    "Thirumangalam": "STI",
    "Koyambedu": "SKO",
    "Arumbakkam": "SAR",
    "Vadapalani": "SVA",
    "Ashok Nagar": "SAN",
    "Ekkattuthangal": "SSI",
    "Arignar Anna Alandur ": "SAL",
    "St. Thomas Mount": "SMM",
    # Yellow Line (corridor 4)
    "Poonamallee Bypass Metro": "POB",
    "Poonamallee Government Hospital Metro": "MUL",
    "Karayanchavadi Metro": "KAC",
    "Kattupakkam Metro": "KPM",
    "Iyyapanthangal Metro": "IYP",
    "Thelliyaragaram Metro": "THL",
    "Porur Junction Metro": "PRJ",
}


def convert_parking_station_code(station_name):
    return PARKING_STATION_CODES.get(station_name, station_name)


def normalize_daily(payload, dataset_date):
    vehicle_types = sorted(key for key in payload if key != "totalVehicles")
    return pd.DataFrame([{
        "Date": dataset_date,
        "Total Vehicles": payload["totalVehicles"],
        **{name: payload[name] for name in vehicle_types},
    }])


def normalize_hourly(payload, dataset_date):
    series = {item["name"]: item["data"] for item in payload["series"]}
    vehicle_types = sorted(name for name in series if name != "Total Vehicles")
    return pd.DataFrame([{
        "Date": dataset_date,
        "Hour": time[:5],
        "Total Vehicles": series["Total Vehicles"][index],
        **{name: series[name][index] for name in vehicle_types},
    } for index, time in enumerate(payload["categories"])])


def normalize_station(payload, dataset_date):
    rows = []
    for line in payload:
        series = {item["name"]: item["data"] for item in line["series"]}
        vehicle_types = sorted(name for name in series if name != "Total Vehicles")
        for index, station in enumerate(line["categories"]):
            rows.append({
                "Date": dataset_date,
                "Line": line["line"],
                "Station": convert_parking_station_code(station),
                "Total Vehicles": series["Total Vehicles"][index],
                **{name: series[name][index] for name in vehicle_types},
            })
    return pd.DataFrame(rows)


def collect_parking(output_dir="data", api_response=None, anomaly_log=ANOMALY_LOG_PATH):
    daily = fetch_json(BASE_URL + "allTicketCount/1", api_response)
    hourly = fetch_json(BASE_URL + "hourlybaseddata/1", api_response)
    station = fetch_json(BASE_URL + "stationData/1", api_response)
    dataset_date = os.getenv("DATASET_DATE")
    if not dataset_date:
        ridership_hourly = fetch_json(RIDERSHIP_HOURLY_URL, api_response)
        dataset_date = pd.to_datetime(ridership_hourly["categories"][0]).strftime("%Y-%m-%d")

    datasets = (
        (DAILY_FILENAME, normalize_daily(daily, dataset_date), PARKING_DAILY),
        (HOURLY_FILENAME, normalize_hourly(hourly, dataset_date), PARKING_HOURLY),
        (STATION_FILENAME, normalize_station(station, dataset_date), PARKING_STATION),
    )
    # Validate every archive and payload before migrating or writing any CSV.
    results = [
        upsert_rows(read_csv(Path(output_dir) / filename), dataframe, schema)
        for filename, dataframe, schema in datasets
    ]
    for (filename, _, schema), result in zip(datasets, results):
        write_csv_atomic(Path(output_dir) / filename, result.dataframe)
        print(f"{schema.name}: inserted={result.inserted}, updated={result.updated}, anomalies={result.anomalies}")
    append_anomaly_record(
        Path(output_dir) / anomaly_log, source="parking", day="1",
        dataset_date=dataset_date, results=results,
    )
    return results


def main():
    parser = argparse.ArgumentParser(description="Collect Chennai Metro parking data.")
    parser.add_argument("--api-response", type=Path, help="Read a JSON response bundle instead of calling the API")
    parser.add_argument("--output-dir", default="data", help="Data root containing the parking output folder")
    parser.add_argument("--anomaly-log", type=Path, default=ANOMALY_LOG_PATH)
    args = parser.parse_args()
    results = collect_parking(args.output_dir, args.api_response, args.anomaly_log)
    if has_anomalies(results):
        raise SystemExit(ANOMALY_EXIT_CODE)


if __name__ == "__main__":
    main()
