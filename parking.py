import requests
import pandas as pd
import os

"""
SETUP
"""

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
}

def convert_parking_station_code(station_name):
    return PARKING_STATION_CODES.get(station_name, station_name)

"""
CSV VALIDATION
"""

last_daily_date = None
last_hourly_date = None
last_station_date = None

if not os.path.exists(DAILY_FILENAME):
    pd.DataFrame().to_csv(DAILY_FILENAME, index=False)
else:
    try:
        daily_df = pd.read_csv(DAILY_FILENAME)
        if not daily_df.empty:
            last_daily_date = daily_df['Date'].iloc[-1]
    except pd.errors.EmptyDataError:
        pass

if not os.path.exists(HOURLY_FILENAME):
    pd.DataFrame().to_csv(HOURLY_FILENAME, index=False)
else:
    try:
        hourly_df = pd.read_csv(HOURLY_FILENAME)
        if not hourly_df.empty:
            last_hourly_date = hourly_df['Date'].iloc[-1]
    except pd.errors.EmptyDataError:
        pass

if not os.path.exists(STATION_FILENAME):
    pd.DataFrame().to_csv(STATION_FILENAME, index=False)
else:
    try:
        station_df = pd.read_csv(STATION_FILENAME)
        if not station_df.empty:
            last_station_date = station_df['Date'].iloc[-1]
    except pd.errors.EmptyDataError:
        pass

"""
DATA COLLECTION AND BASIC PROCESSING
"""

def get_daily_parking():
    return requests.get(DAILY_PARKING_URL).json()

def get_hourly_parking():
    response = requests.get(HOURLY_PARKING_URL).json()
    hourly_data = {
        'times': [t[:5] for t in response['categories']],
        'series': {s['name']: s['data'] for s in response['series']}
    }
    return hourly_data

def get_station_parking():
    response = requests.get(STATION_PARKING_URL).json()
    station_data = {}
    for line_data in response:
        line_number = line_data['line']
        station_data[line_number] = {
            'stations': list(map(convert_parking_station_code, line_data['categories'])),
            'series': {s['name']: s['data'] for s in line_data['series']}
        }
    return station_data

daily_parking = get_daily_parking()
hourly_parking = get_hourly_parking()
station_parking = get_station_parking()

# Extract date from ridership API
ridership_hourly_response = requests.get(RIDERSHIP_HOURLY_URL).json()
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
    # Extract vehicle types (all keys except totalVehicles)
    vehicle_types = sorted([key for key in daily_parking.keys() if key != 'totalVehicles'])
    
    # Create headers
    daily_headers = ['Date', 'Total Vehicles'] + vehicle_types
    
    # Build row with values
    daily_row = {
        'Date': DATE,
        'Total Vehicles': daily_parking['totalVehicles']
    }
    for vtype in vehicle_types:
        daily_row[vtype] = daily_parking[vtype]
    
    # Create DataFrame and append
    daily_df_new = pd.DataFrame([daily_row])
    
    if last_daily_date is None:
        daily_df_new.to_csv(DAILY_FILENAME, index=False, header=True)
        print(f"Created {DAILY_FILENAME} with data for {DATE}")
    else:
        daily_df_new.to_csv(DAILY_FILENAME, index=False, mode='a', header=False)
        print(f"Appended daily parking data for {DATE}")
else:
    print(f"Daily parking data for {DATE} already exists. Skipping.")

# ===== HOURLY CSV =====
if DATE != last_hourly_date:
    # Extract vehicle types from series (exclude 'Total Vehicles')
    hourly_vehicle_types = sorted([key for key in hourly_parking['series'].keys() if key != 'Total Vehicles'])
    
    # Create headers
    hourly_headers = ['Date', 'Hour', 'Total Vehicles'] + hourly_vehicle_types
    
    # Build rows by iterating through times
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
    
    # Create DataFrame and append
    hourly_df_new = pd.DataFrame(hourly_rows)
    
    if last_hourly_date is None:
        hourly_df_new.to_csv(HOURLY_FILENAME, index=False, header=True)
        print(f"Created {HOURLY_FILENAME} with {len(hourly_rows)} entries for {DATE}")
    else:
        hourly_df_new.to_csv(HOURLY_FILENAME, index=False, mode='a', header=False)
        print(f"Appended {len(hourly_rows)} hourly parking entries for {DATE}")
else:
    print(f"Hourly parking data for {DATE} already exists. Skipping.")

# ===== STATIONWISE CSV =====
if DATE != last_station_date:
    # Extract vehicle types from first line's series (should be consistent across lines)
    first_line_key = list(station_parking.keys())[0]
    station_vehicle_types = sorted([key for key in station_parking[first_line_key]['series'].keys() if key != 'Total Vehicles'])
    
    # Create headers
    station_headers = ['Date', 'Line', 'Station', 'Total Vehicles'] + station_vehicle_types
    
    # Build rows by iterating through lines and stations
    station_rows = []
    for line_number, line_data in station_parking.items():
        stations = line_data['stations']
        # Get vehicle types for this specific line (in case they differ)
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
    
    # Create DataFrame and append
    station_df_new = pd.DataFrame(station_rows)
    
    if last_station_date is None:
        station_df_new.to_csv(STATION_FILENAME, index=False, header=True)
        print(f"Created {STATION_FILENAME} with {len(station_rows)} entries for {DATE}")
    else:
        station_df_new.to_csv(STATION_FILENAME, index=False, mode='a', header=False)
        print(f"Appended {len(station_rows)} station parking entries for {DATE}")
else:
    print(f"Station parking data for {DATE} already exists. Skipping.")
