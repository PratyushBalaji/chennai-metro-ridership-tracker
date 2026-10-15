import argparse
import json
import requests
import pandas as pd
import os

from collector import append_csv, read_csv

"""
SETUP
"""

parser = argparse.ArgumentParser(description="Collect Chennai Metro ridership data.")
parser.add_argument("--api-response", help="Read a JSON response bundle instead of calling the API")
parser.add_argument("--output-dir", default=".", help="Directory containing the Ridership output folder")
args = parser.parse_args()

DAILY_FILENAME = os.path.join(args.output_dir, "Ridership/ChennaiMetro_Daily_Ridership.csv")
HOURLY_FILENAME = os.path.join(args.output_dir, "Ridership/ChennaiMetro_Hourly_Ridership.csv")
STATION_FILENAME = os.path.join(args.output_dir, "Ridership/ChennaiMetro_Station_Ridership.csv")
os.makedirs(os.path.dirname(DAILY_FILENAME), exist_ok=True)

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
DAY = "1" # '1' for previous day, '0' for current day
# Note : Depending on time of day, /0 may have incomplete data. Since we would rather deal with complete data, we use /1.
# If we use /0 and data is incomplete, the rest of that day's data won't be updated until incomplete data is manually removed from CSVs.
# (With current validation logic)

DAILY_TICKET_COUNT_URL = BASE_URL + "allTicketCount/" + DAY
HOURLY_PASSENGER_DATA_URL = BASE_URL + "hourlybaseddata/" + DAY
STATION_FLOW_DATA_URL = BASE_URL + "stationData/" + DAY

STATION_CODES = { # one-to-one mapping -> more compact and unambiguous
    # Blue Line
    "WIMCO NAGAR DEPOT":"SWD",
    "WIMCO NAGAR METRO":"SWN",
    "THIRUVOTRIYUR METRO":"STV",
    "THIRUVOTRIYUR THERADI METRO":"STT",
    "KALADIPET METRO":"SKP",
    "TOLLGATE METRO":"STG",
    "NEW WASHERMENPET METRO":"SNW",
    "TONDIARPET METRO":"STR",
    "THIYAGARAYA COLLEGE METRO":"STC",
    "WASHERMANPET":"SWA",
    "MANNADI":"SMA",
    "HIGH COURT":"SHC",
    "GOVERNMENT ESTATE":"SGE",
    "LIC":"SLI",
    "THOUSAND LIGHT":"STL",
    "AG-DMS":"SGM",
    "TEYNAMPET":"STE",
    "NANDANAM":"SCR",
    "SAIDAPET":"SSA",
    "LITTLE MOUNT":"SLM",
    "GUINDY":"SGU",
    "OTA - NANGANALLUR ROAD":"SOT",
    "MEENAMBAKKAM":"SME",
    "CHENNAI AIRPORT":"SAP",

    # Green Line
    "EGMORE":"SEG",
    "NEHRU PARK":"SNP",
    "KILPAUK":"SKM",
    "PACHAIAPPA S COLLEGE":"SPC",
    "SHENOY NAGAR":"SSN",
    "ANNA NAGAR EAST":"SAE",
    "ANNA NAGAR TOWER":"SAT",
    "THIRUMANGALAM":"STI",
    "KOYAMBEDU":"SKO",
    "CMBT":"SCM",
    "ARUMBAKKAM":"SAR",
    "ASHOK NAGAR":"SAN",
    "EKKATTUTHANGAL":"SSI",
    "St. THOMAS MOUNT":"SMM",

    # Yellow Line
    "POONAMALLEE BYPASS METRO":"PBYM",
    # Temporary five-letter placeholders; replace when published station codes are available.
    "POONAMALLEE BUS STAND METRO":"PNMBS",
    "POONAMALLEE GOVERNMENT HOSPITAL METRO":"PNMGH",
    "KARAYANCHAVADI METRO":"KRYCH",
    "KUMANANCHAVADI METRO":"KMNCH",
    "KATTUPAKKAM METRO":"KTTPK",
    "IYAPPANTHANGAL METRO":"IYPTH",
    "THELLIYARAGARAM METRO":"THLYR",
    "PORUR BYPASS METRO":"PRBYP",
    "PORUR JUNCTION METRO":"PRJNC",

    # Interchange stations
    "VADAPALANI":"SVA",
    "CENTRAL  METRO":"SCC",
    "ALANDUR":"SAL",
}

def convert_station_code(station_name):
    return STATION_CODES.get(station_name, station_name)

"""
CSV VALIDATION
"""

daily_df = read_csv(DAILY_FILENAME)
hourly_df = read_csv(HOURLY_FILENAME)
station_df = read_csv(STATION_FILENAME)

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


def get_daily_ticket_count():
    return fetch_json(DAILY_TICKET_COUNT_URL, args.api_response)

def get_hourly_passenger_data():
    response = fetch_json(HOURLY_PASSENGER_DATA_URL, args.api_response)
    hourly_data = {
        'date': pd.to_datetime(response['categories'][0]).strftime('%Y-%m-%d'),
        'timestamps': [pd.to_datetime(ts).strftime('%H:%M') for ts in response['categories']],
        'series': {s['name']: s['data'] for s in response['series']}
    }
    return hourly_data

def get_station_flow_data():
    response = fetch_json(STATION_FLOW_DATA_URL, args.api_response)
    station_data = {}
    for line_data in response:
        line_number = line_data['line']
        station_data[line_number] = {
            'stations': list(map(convert_station_code, line_data['categories'])),
            'series': {s['name']: s['data'] for s in line_data['series']}
        }
    return station_data

daily_tickets = get_daily_ticket_count()
hourly_passengers = get_hourly_passenger_data()
station_flows = get_station_flow_data()

DATE = hourly_passengers['date'] # yyyy-mm-dd

"""
DATA VALIDATION
"""

# Skipped for now

"""
OUTPUT FORMATTING AND APPEND TO FILES
"""

# DAILY CSV
if DATE != last_daily_date: # if not already at latest date
    payment_methods = sorted([key for key in daily_tickets.keys() if key.startswith('noOf')])
    daily_headers = ['Date', 'Total'] + payment_methods
    
    daily_row = {
        'Date': DATE,
        'Total': daily_tickets['totalTickets']
    }
    for method in payment_methods:
        daily_row[method] = daily_tickets[method]
    
    daily_df_new = pd.DataFrame([daily_row])
    
    append_csv(DAILY_FILENAME, daily_df, daily_df_new, zero_columns=("noOfUTPQR",))
    print(f"Saved daily ridership data for {DATE}")
else:
    print(f"Daily data for {DATE} already exists. Skipping.")

# HOURLY CSV
if DATE != last_hourly_date:
    hourly_payment_methods = sorted([key for key in hourly_passengers['series'].keys() if key != 'Total']) # total first
    
    hourly_headers = ['Date', 'Hour', 'Total'] + hourly_payment_methods
    
    hourly_rows = []
    for i, timestamp in enumerate(hourly_passengers['timestamps']):
        row = {
            'Date': DATE,
            'Hour': timestamp,
            'Total': hourly_passengers['series']['Total'][i]
        }
        for method in hourly_payment_methods:
            row[method] = hourly_passengers['series'][method][i]
        hourly_rows.append(row)
    
    hourly_df_new = pd.DataFrame(hourly_rows)
    
    append_csv(HOURLY_FILENAME, hourly_df, hourly_df_new, zero_columns=("UTP QR",))
    print(f"Saved hourly ridership data for {DATE}")
else:
    print(f"Hourly data for {DATE} already exists. Skipping.")

# STATIONWISE CSV
if DATE != last_station_date:
    station_payment_methods = sorted({key for line in station_flows.values() for key in line['series'] if key != 'Total'})
    
    station_headers = ['Date', 'Line', 'Station', 'Total'] + station_payment_methods
    
    station_rows = []
    for line_number, line_data in station_flows.items():
        stations = line_data['stations']
        for i, station_code in enumerate(stations):
            row = {
                'Date': DATE,
                'Line': line_number,
                'Station': station_code,
                'Total': line_data['series']['Total'][i]
            }
            for method in station_payment_methods:
                row[method] = line_data['series'][method][i] if method in line_data['series'] else None
            station_rows.append(row)
    
    station_df_new = pd.DataFrame(station_rows, columns=station_headers, dtype=object)
    
    append_csv(STATION_FILENAME, station_df, station_df_new, zero_columns=("UTP QR",))
    print(f"Saved station ridership data for {DATE}")
else:
    print(f"Station data for {DATE} already exists. Skipping.")
