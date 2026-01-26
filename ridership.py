import requests
import pandas as pd
import os

"""
SETUP
"""

DAILY_FILENAME = "Ridership/ChennaiMetro_Daily_Ridership.csv"
HOURLY_FILENAME = "Ridership/ChennaiMetro_Hourly_Ridership.csv"
STATION_FILENAME = "Ridership/ChennaiMetro_Station_Ridership.csv"

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
    "VADAPALANI":"SVA",
    "ASHOK NAGAR":"SAN",
    "EKKATTUTHANGAL":"SSI",
    "St. THOMAS MOUNT":"SMM",

    # Interchange station
    "CENTRAL  METRO":"SCC",
    "ALANDUR":"SAL",
}

def convert_station_code(station_name):
    return STATION_CODES.get(station_name, station_name)

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

def get_daily_ticket_count():
    return requests.get(DAILY_TICKET_COUNT_URL).json()

def get_hourly_passenger_data():
    response = requests.get(HOURLY_PASSENGER_DATA_URL).json()
    hourly_data = {
        'date': pd.to_datetime(response['categories'][0]).strftime('%Y-%m-%d'),
        'timestamps': [pd.to_datetime(ts).strftime('%H:%M') for ts in response['categories']],
        'series': {s['name']: s['data'] for s in response['series']}
    }
    return hourly_data

def get_station_flow_data():
    response = requests.get(STATION_FLOW_DATA_URL).json()
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
    
    if last_daily_date is None: # no first entry
        daily_df_new.to_csv(DAILY_FILENAME, index=False, header=True)
        print(f"Created {DAILY_FILENAME} with data for {DATE}") # create new file
    else: # else append
        daily_df_new.to_csv(DAILY_FILENAME, index=False, mode='a', header=False)
        print(f"Appended daily data for {DATE}")
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
    
    if last_hourly_date is None:
        hourly_df_new.to_csv(HOURLY_FILENAME, index=False, header=True)
        print(f"Created {HOURLY_FILENAME} with {len(hourly_rows)} entries for {DATE}")
    else:
        hourly_df_new.to_csv(HOURLY_FILENAME, index=False, mode='a', header=False)
        print(f"Appended {len(hourly_rows)} hourly entries for {DATE}")
else:
    print(f"Hourly data for {DATE} already exists. Skipping.")

# STATIONWISE CSV
if DATE != last_station_date:
    first_line_key = list(station_flows.keys())[0]
    station_payment_methods = sorted([key for key in station_flows[first_line_key]['series'].keys() if key != 'Total'])
    
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
                row[method] = line_data['series'][method][i]
            station_rows.append(row)
    
    station_df_new = pd.DataFrame(station_rows)
    
    if last_station_date is None:
        station_df_new.to_csv(STATION_FILENAME, index=False, header=True)
        print(f"Created {STATION_FILENAME} with {len(station_rows)} entries for {DATE}")
    else:
        station_df_new.to_csv(STATION_FILENAME, index=False, mode='a', header=False)
        print(f"Appended {len(station_rows)} station entries for {DATE}")
else:
    print(f"Station data for {DATE} already exists. Skipping.")