import argparse
import json
import requests
import pandas as pd
import os

from collector import append_csv, read_csv

"""
SETUP
"""

parser = argparse.ArgumentParser(description="Collect Chennai Metro PHPDT data.")
parser.add_argument("--api-response", help="Read a JSON response bundle instead of calling the API")
parser.add_argument("--output-dir", default=".", help="Directory containing the PHPDT output folder")
args = parser.parse_args()

DAILY_FILENAME = os.path.join(args.output_dir, "PHPDT/ChennaiMetro_Daily_PHPDT.csv")

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"

PHPDT_URL = BASE_URL + "PHPDTreport/"

ROUTE_MAPPING = {
    # Line 1 (Blue Line)
    "saPtoSWDViewModel": {"line": "1", "direction": "UP", "from": "SAP", "to": "SWD"},
    "swDtoSAPViewModel": {"line": "1", "direction": "DOWN", "from": "SWD", "to": "SAP"},
    
    # Line 2 (Green Line)
    "smMtoSCCViewModel": {"line": "2", "direction": "UP", "from": "SMM", "to": "SCC"},
    "scCtoSMMViewModel": {"line": "2", "direction": "DOWN", "from": "SCC", "to": "SMM"},
    # Line 4 (Yellow Line)
    "poBtoSVAViewModel": {"line": "04", "direction": "UP", "from": "PBYM", "to": "SVA"},
    "svAtoPOBViewModel": {"line": "04", "direction": "DOWN", "from": "SVA", "to": "PBYM"},
}

def extract_station_code(corridor):
    """Extract station codes from route key like 'seG_SCC2' -> ('SEG', 'SCC')"""
    corridor = "".join([i for i in corridor if i.isalpha() or i == '_']) # remove non-alpha chars
    parts = corridor.split("_")
    if len(parts) == 2:
        return tuple("PBYM" if part.upper() == "POB" else part.upper() for part in parts)
    
    return None, None

"""
CSV VALIDATION
"""

phpdt_df = read_csv(DAILY_FILENAME)

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


def get_phpdt_data():
    return fetch_json(PHPDT_URL, args.api_response)

phpdt_response = get_phpdt_data()

unknown_routes = [key for key, data in phpdt_response.items() if data and key not in ROUTE_MAPPING]
if unknown_routes:
    raise ValueError(f"Unknown non-empty PHPDT routes: {', '.join(unknown_routes)}")

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
        print(f"Warning: Route key '{route_key}' not found in PHPDT response")
        continue
    
    route_data = phpdt_response[route_key]
    if not route_data:
        print(f"Warning: No data available for route key '{route_key}'")
        continue
    
    for entry in route_data:
        from_datetime = pd.to_datetime(entry['transfromdate'])
        to_datetime = pd.to_datetime(entry['transtodate'])
        
        date = from_datetime.strftime('%Y-%m-%d')
        start_hour = from_datetime.strftime('%H:%M')
        end_hour = to_datetime.strftime('%H:%M')
        
        line = route_info['line']
        direction = route_info['direction']
        
        # Get all entries in the format : <station1>_<station2>
        corridor_keys = [k for k in entry.keys() if '_' in k]
        
        for key in corridor_keys:            
            corridor_value = entry[key]
            
            # Extract station codes from corridor key
            from_station, to_station = extract_station_code(key)
            
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
            else:
                print(f"Error: Could not extract stations from corridor key '{key}'")

if phpdt_rows:
    DATE = phpdt_rows[0]['Date'] # current date
    
    phpdt_df_new = pd.DataFrame(phpdt_rows)
    keys = ['Date', 'Line', 'Direction', 'Start Station', 'End Station']
    if phpdt_df_new.duplicated(keys).any():
        raise ValueError("PHPDT response contains duplicate corridors for the same day and direction")
    if not phpdt_df.empty:
        # A route can become available after the other routes for the same day.
        known = pd.MultiIndex.from_frame(phpdt_df[keys])
        phpdt_df_new = phpdt_df_new.loc[~pd.MultiIndex.from_frame(phpdt_df_new[keys]).isin(known)]
    if not phpdt_df_new.empty:
        append_csv(DAILY_FILENAME, phpdt_df, phpdt_df_new)
        print(f"Saved {len(phpdt_df_new)} new PHPDT entries for {DATE}")
    else:
        print(f"PHPDT data for {DATE} already exists. Skipping.")
else:
    print("No PHPDT data available to process.")
