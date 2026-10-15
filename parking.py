import argparse
import json
import requests
import pandas as pd
import os

from collector import append_csv, read_csv, normalize_parking_columns, write_csv_atomic

"""
SETUP
"""

parser = argparse.ArgumentParser(description="Collect Chennai Metro parking data.")
parser.add_argument("--api-response", help="Read a JSON response bundle instead of calling the API")
parser.add_argument("--output-dir", default=".", help="Directory containing the Parking output folder")
args = parser.parse_args()

DAILY_FILENAME = os.path.join(args.output_dir, "Parking/ChennaiMetro_Daily_Parking.csv")
HOURLY_FILENAME = os.path.join(args.output_dir, "Parking/ChennaiMetro_Hourly_Parking.csv")
STATION_FILENAME = os.path.join(args.output_dir, "Parking/ChennaiMetro_Station_Parking.csv")
os.makedirs(os.path.dirname(DAILY_FILENAME), exist_ok=True)

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/parkingdashboard/"
DAY = "1"

DAILY_PARKING_URL = BASE_URL + "allTicketCount/" + DAY
HOURLY_PARKING_URL = BASE_URL + "hourlybaseddata/" + DAY
STATION_PARKING_URL = BASE_URL + "stationData/" + DAY

RIDERSHIP_BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
RIDERSHIP_HOURLY_URL = RIDERSHIP_BASE_URL + "hourlybaseddata/" + DAY

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

    # Yellow Line; match ridership's temporary codes.
    "Poonamallee Bypass Metro": "PBYM",
    "Poonamallee Government Hospital Metro": "PNMGH",
    "Karayanchavadi Metro": "KRYCH",
    "Kattupakkam Metro": "KTTPK",
    "Iyyapanthangal Metro": "IYPTH",
    "Thelliyaragaram Metro": "THLYR",
    "Porur Junction Metro": "PRJNC",
}

def convert_parking_station_code(station_name):
    return PARKING_STATION_CODES.get(station_name, station_name)

"""
CSV VALIDATION
"""

daily_df = read_csv(DAILY_FILENAME)
hourly_df = read_csv(HOURLY_FILENAME)
station_df = read_csv(STATION_FILENAME)
station_header_changed = 'Eight Wheleer' in station_df
station_df = normalize_parking_columns(station_df)

last_daily_date = daily_df["Date"].iloc[-1] if not daily_df.empty else None
last_hourly_date = hourly_df["Date"].iloc[-1] if not hourly_df.empty else None
last_station_date = station_df["Date"].iloc[-1] if not station_df.empty else None

"""
DATA COLLECTION AND BASIC PROCESSING
"""

def fetch_json(url, api_response=None):
    if api_response is not None:
        with open(api_response, encoding="utf-8") as file:
            bundle = json.load(file)
        if not isinstance(bundle, dict) or not isinstance(bundle.get("responses"), dict):
            raise ValueError("API response file must contain a 'responses' object keyed by URL")
        if url not in bundle["responses"]:
            raise ValueError(f"API response file has no response for {url}")
        return bundle["responses"][url]
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def get_daily_parking():
    return fetch_json(DAILY_PARKING_URL, args.api_response)

def get_hourly_parking():
    response = fetch_json(HOURLY_PARKING_URL, args.api_response)
    hourly_data = {
        'times': [t[:5] for t in response['categories']],
        'series': {s['name']: s['data'] for s in response['series']}
    }
    return hourly_data

def get_station_parking():
    response = fetch_json(STATION_PARKING_URL, args.api_response)
    station_data = {}
    for line_data in response:
        normalize_parking_columns(pd.DataFrame({s['name']: s['data'] for s in line_data['series']}))
        line_number = line_data['line']
        station_data[line_number] = {
            'stations': list(map(convert_parking_station_code, line_data['categories'])),
            'series': {s['name']: s['data'] for s in line_data['series']}
        }
    return station_data

daily_parking = get_daily_parking()
hourly_parking = get_hourly_parking()
station_parking = get_station_parking()

# Prefer date provided via environment (to avoid extra API calls in CI)
_env_date = os.getenv("DATASET_DATE")
if _env_date:
    DATE = _env_date
else:
    # Fallback : Extract date from ridership API
    ridership_hourly_response = fetch_json(RIDERSHIP_HOURLY_URL, args.api_response)
    DATE = pd.to_datetime(ridership_hourly_response['categories'][0]).strftime('%Y-%m-%d')

"""
DATA VALIDATION
"""

# Skipped for now

"""
OUTPUT FORMATTING AND APPEND TO FILES
"""

# ===== DAILY CSV =====
if DATE != last_daily_date:
    vehicle_types = sorted([key for key in daily_parking.keys() if key != 'totalVehicles'])
    
    daily_headers = ['Date', 'Total Vehicles'] + vehicle_types
    
    daily_row = {
        'Date': DATE,
        'Total Vehicles': daily_parking['totalVehicles']
    }
    for vtype in vehicle_types:
        daily_row[vtype] = daily_parking[vtype]
    
    daily_df_new = pd.DataFrame([daily_row])
    
    append_csv(DAILY_FILENAME, daily_df, daily_df_new)
    print(f"Saved daily parking data for {DATE}")
else:
    print(f"Daily parking data for {DATE} already exists. Skipping.")

# ===== HOURLY CSV =====
if DATE != last_hourly_date:
    hourly_vehicle_types = sorted([key for key in hourly_parking['series'].keys() if key != 'Total Vehicles'])
    
    hourly_headers = ['Date', 'Hour', 'Total Vehicles'] + hourly_vehicle_types
    
    hourly_rows = []
    for i, time in enumerate(hourly_parking['times']):
        row = {
            'Date': DATE,
            'Hour': time,
            'Total Vehicles': hourly_parking['series']['Total Vehicles'][i]
        }
        for vtype in hourly_vehicle_types:
            row[vtype] = hourly_parking['series'][vtype][i]
        hourly_rows.append(row)
    
    hourly_df_new = pd.DataFrame(hourly_rows)
    
    append_csv(HOURLY_FILENAME, hourly_df, hourly_df_new)
    print(f"Saved hourly parking data for {DATE}")
else:
    print(f"Hourly parking data for {DATE} already exists. Skipping.")

# ===== STATIONWISE CSV =====
if DATE != last_station_date:
    first_line_key = list(station_parking.keys())[0]
    station_vehicle_types = sorted([key for key in station_parking[first_line_key]['series'].keys() if key != 'Total Vehicles'])
    
    station_headers = ['Date', 'Line', 'Station', 'Total Vehicles'] + station_vehicle_types
    
    station_rows = []
    for line_number, line_data in station_parking.items():
        stations = line_data['stations']
        line_vehicle_types = sorted([key for key in line_data['series'].keys() if key != 'Total Vehicles'])
        for i, station_code in enumerate(stations):
            row = {
                'Date': DATE,
                'Line': line_number,
                'Station': station_code,
                'Total Vehicles': line_data['series']['Total Vehicles'][i]
            }
            for vtype in line_vehicle_types:
                row[vtype] = line_data['series'][vtype][i]
            station_rows.append(row)
    
    station_df_new = normalize_parking_columns(pd.DataFrame(station_rows))
    
    append_csv(STATION_FILENAME, station_df, station_df_new)
    print(f"Saved station parking data for {DATE}")
else:
    if station_header_changed:
        write_csv_atomic(STATION_FILENAME, station_df)
    print(f"Station parking data for {DATE} already exists. Skipping.")
