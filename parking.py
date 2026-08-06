import os

import pandas as pd
import requests

from monotonic_updates import read_csv_or_empty, upsert_monotonic

DAILY_FILENAME = "Parking/ChennaiMetro_Daily_Parking.csv"
HOURLY_FILENAME = "Parking/ChennaiMetro_Hourly_Parking.csv"
STATION_FILENAME = "Parking/ChennaiMetro_Station_Parking.csv"

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/"
DAY = "1"

DAILY_PARKING_URL = BASE_URL + "allTicketCount/" + DAY
HOURLY_PARKING_URL = BASE_URL + "hourlybaseddata/" + DAY
STATION_PARKING_URL = BASE_URL + "stationData/" + DAY

RIDERSHIP_BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
RIDERSHIP_HOURLY_URL = RIDERSHIP_BASE_URL + "hourlybaseddata/" + DAY

PARKING_STATION_CODES = {
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
}


def convert_parking_station_code(station_name):
    return PARKING_STATION_CODES.get(station_name, station_name)


def get_daily_parking():
    return requests.get(DAILY_PARKING_URL).json()


def get_hourly_parking():
    response = requests.get(HOURLY_PARKING_URL).json()
    return {
        "times": [t[:5] for t in response["categories"]],
        "series": {s["name"]: s["data"] for s in response["series"]},
    }


def get_station_parking():
    response = requests.get(STATION_PARKING_URL).json()
    station_data = {}
    for line_data in response:
        line_number = line_data["line"]
        station_data[line_number] = {
            "stations": list(map(convert_parking_station_code, line_data["categories"])),
            "series": {s["name"]: s["data"] for s in line_data["series"]},
        }
    return station_data


daily_parking = get_daily_parking()
hourly_parking = get_hourly_parking()
station_parking = get_station_parking()

_env_date = os.getenv("DATASET_DATE")
if _env_date:
    DATE = _env_date
else:
    ridership_hourly_response = requests.get(RIDERSHIP_HOURLY_URL).json()
    DATE = pd.to_datetime(ridership_hourly_response["categories"][0]).strftime("%Y-%m-%d")

# DAILY
vehicle_types = sorted([key for key in daily_parking.keys() if key != "totalVehicles"])
daily_row = {"Date": DATE, "Total Vehicles": daily_parking["totalVehicles"]}
for vtype in vehicle_types:
    daily_row[vtype] = daily_parking[vtype]
daily_df_new = pd.DataFrame([daily_row])
daily_df_existing = read_csv_or_empty(DAILY_FILENAME)
daily_df_updated, daily_stats = upsert_monotonic(
    existing_df=daily_df_existing,
    new_df=daily_df_new,
    key_cols=["Date"],
    dataset_name="parking-daily",
)
daily_df_updated.to_csv(DAILY_FILENAME, index=False)
print(f"Daily parking update complete for {DATE}: {daily_stats}")

# HOURLY
hourly_vehicle_types = sorted([key for key in hourly_parking["series"].keys() if key != "Total Vehicles"])
hourly_rows = []
for i, time in enumerate(hourly_parking["times"]):
    row = {
        "Date": DATE,
        "Hour": time,
        "Total Vehicles": hourly_parking["series"]["Total Vehicles"][i],
    }
    for vtype in hourly_vehicle_types:
        row[vtype] = hourly_parking["series"][vtype][i]
    hourly_rows.append(row)
hourly_df_new = pd.DataFrame(hourly_rows)
hourly_df_existing = read_csv_or_empty(HOURLY_FILENAME)
hourly_df_updated, hourly_stats = upsert_monotonic(
    existing_df=hourly_df_existing,
    new_df=hourly_df_new,
    key_cols=["Date", "Hour"],
    dataset_name="parking-hourly",
)
hourly_df_updated.to_csv(HOURLY_FILENAME, index=False)
print(f"Hourly parking update complete for {DATE}: {hourly_stats}")

# STATIONWISE
first_line_key = list(station_parking.keys())[0]
station_vehicle_types = sorted([key for key in station_parking[first_line_key]["series"].keys() if key != "Total Vehicles"])
station_rows = []
for line_number, line_data in station_parking.items():
    stations = line_data["stations"]
    for i, station_code in enumerate(stations):
        row = {
            "Date": DATE,
            "Line": line_number,
            "Station": station_code,
            "Total Vehicles": line_data["series"]["Total Vehicles"][i],
        }
        for vtype in station_vehicle_types:
            row[vtype] = line_data["series"][vtype][i]
        station_rows.append(row)
station_df_new = pd.DataFrame(station_rows)
station_df_existing = read_csv_or_empty(STATION_FILENAME)
station_df_updated, station_stats = upsert_monotonic(
    existing_df=station_df_existing,
    new_df=station_df_new,
    key_cols=["Date", "Line", "Station"],
    dataset_name="parking-stationwise",
)
station_df_updated.to_csv(STATION_FILENAME, index=False)
print(f"Station parking update complete for {DATE}: {station_stats}")
