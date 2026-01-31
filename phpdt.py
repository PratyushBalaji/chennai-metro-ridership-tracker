import requests
import pandas as pd
import os

"""
SETUP
"""

DAILY_FILENAME = "PHPDT/ChennaiMetro_Daily_PHPDT.csv"

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
DAY = "1"

PHPDT_URL = BASE_URL + "PHPDTreport/"

# Mapping of route names to line numbers and directions
ROUTE_MAPPING = {
    # Line 1 (Blue Line)
    "saPtoSWDViewModel": {"line": "1", "direction": "UP", "from": "SAP", "to": "SWD"},
    "swDtoSAPViewModel": {"line": "1", "direction": "DOWN", "from": "SWD", "to": "SAP"},
    
    # Line 2 (Green Line)
    "smMtoSCCViewModel": {"line": "2", "direction": "UP", "from": "SMM", "to": "SCC"},
    "scCtoSMMViewModel": {"line": "2", "direction": "DOWN", "from": "SCC", "to": "SMM"},
}

def extract_station_code(corridor):
    """Extract station codes from route key like 'seG_SCC2' -> ('SEG', 'SCC')"""
    corridor = "".join([i for i in corridor if i.isalpha() or i == '_'])
    parts = corridor.split("_")
    if len(parts) == 2:
        return parts[0].upper(), parts[1].upper()
    
    return None, None

"""
CSV VALIDATION
"""

last_phpdt_date = None

if not os.path.exists(DAILY_FILENAME):
    os.makedirs(os.path.dirname(DAILY_FILENAME), exist_ok=True)
    pd.DataFrame().to_csv(DAILY_FILENAME, index=False)
else:
    try:
        phpdt_df = pd.read_csv(DAILY_FILENAME)
        if not phpdt_df.empty:
            last_phpdt_date = phpdt_df['Date'].iloc[-1]
    except pd.errors.EmptyDataError:
        pass

"""
DATA COLLECTION AND BASIC PROCESSING
"""

def get_phpdt_data():
    return requests.get(PHPDT_URL).json()

phpdt_response = get_phpdt_data()

"""
DATA VALIDATION
"""

# Skipped for now

"""
OUTPUT FORMATTING AND APPEND TO FILES
"""

# PHPDT CSV
phpdt_rows = []

for route_key, route_info in ROUTE_MAPPING.items():
    if route_key not in phpdt_response:
        continue
    
    route_data = phpdt_response[route_key]
    if not isinstance(route_data, list) or len(route_data) == 0:
        continue
    
    for entry in route_data:
        # Extract date and time
        from_datetime = pd.to_datetime(entry['transfromdate'])
        to_datetime = pd.to_datetime(entry['transtodate'])
        
        date = from_datetime.strftime('%Y-%m-%d')
        start_hour = from_datetime.strftime('%H:%M')
        end_hour = to_datetime.strftime('%H:%M')
        
        line = route_info['line']
        direction = route_info['direction']
        
        # Process all corridor entries (skip the first entry which has count 0)
        # The route_key tells us the direction endpoints
        # We need to extract all intermediate corridors
        
        # Get all corridor keys from the entry (they follow pattern like 'saP_SME', 'smE_SOT', etc.)
        corridor_keys = [k for k in entry.keys() if '_' in k]
        
        for i, corridor_key in enumerate(corridor_keys):            
            corridor_value = entry[corridor_key]
            
            # Extract station codes from corridor key
            from_station, to_station = extract_station_code(corridor_key)
            
            if from_station and to_station:
                row = {
                    'Date': date,
                    'Line': line,
                    'Direction': direction,
                    'Start Hour': start_hour,
                    'End Hour': end_hour,
                    'Start Station': from_station,
                    'End Station': to_station,
                    'PHPDT': corridor_value
                }
                phpdt_rows.append(row)

if phpdt_rows:
    # Extract unique date from first row
    DATE = phpdt_rows[0]['Date']
    
    if DATE != last_phpdt_date:  # if not already at latest date
        phpdt_df_new = pd.DataFrame(phpdt_rows)
        
        if last_phpdt_date is None:  # no first entry
            phpdt_df_new.to_csv(DAILY_FILENAME, index=False, header=True)
            print(f"Created {DAILY_FILENAME} with {len(phpdt_rows)} entries for {DATE}")
        else:  # else append
            phpdt_df_new.to_csv(DAILY_FILENAME, index=False, mode='a', header=False)
            print(f"Appended {len(phpdt_rows)} PHPDT entries for {DATE}")
    else:
        print(f"PHPDT data for {DATE} already exists. Skipping.")
else:
    print("No PHPDT data available to process.")
