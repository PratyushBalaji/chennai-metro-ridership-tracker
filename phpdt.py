import pandas as pd
import requests

from monotonic_updates import read_csv_or_empty, upsert_monotonic

DAILY_FILENAME = "PHPDT/ChennaiMetro_Daily_PHPDT.csv"
BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
PHPDT_URL = BASE_URL + "PHPDTreport/"

ROUTE_MAPPING = {
    "saPtoSWDViewModel": {"line": "1", "direction": "UP"},
    "swDtoSAPViewModel": {"line": "1", "direction": "DOWN"},
    "smMtoSCCViewModel": {"line": "2", "direction": "UP"},
    "scCtoSMMViewModel": {"line": "2", "direction": "DOWN"},
}


def extract_station_code(corridor):
    corridor = "".join([i for i in corridor if i.isalpha() or i == "_"])
    parts = corridor.split("_")
    if len(parts) == 2:
        return parts[0].upper(), parts[1].upper()
    return None, None


def get_phpdt_data():
    return requests.get(PHPDT_URL).json()


phpdt_response = get_phpdt_data()
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
        from_datetime = pd.to_datetime(entry["transfromdate"])
        to_datetime = pd.to_datetime(entry["transtodate"])

        date = from_datetime.strftime("%Y-%m-%d")
        start_hour = from_datetime.strftime("%H:%M")
        end_hour = to_datetime.strftime("%H:%M")
        line = route_info["line"]
        direction = route_info["direction"]

        corridor_keys = [k for k in entry.keys() if "_" in k]
        for key in corridor_keys:
            from_station, to_station = extract_station_code(key)
            if not (from_station and to_station):
                print(f"Error: Could not extract stations from corridor key '{key}'")
                continue

            phpdt_rows.append(
                {
                    "Date": date,
                    "Line": line,
                    "Direction": direction,
                    "Start Hour": start_hour,
                    "End Hour": end_hour,
                    "Start Station": from_station,
                    "End Station": to_station,
                    "PHPDT": entry[key],
                }
            )

if not phpdt_rows:
    print("No PHPDT data available to process.")
else:
    current_date = phpdt_rows[0]["Date"]
    phpdt_df_new = pd.DataFrame(phpdt_rows)
    phpdt_df_existing = read_csv_or_empty(DAILY_FILENAME)
    phpdt_df_updated, phpdt_stats = upsert_monotonic(
        existing_df=phpdt_df_existing,
        new_df=phpdt_df_new,
        key_cols=["Date", "Line", "Direction", "Start Hour", "End Hour", "Start Station", "End Station"],
        dataset_name="phpdt-daily",
    )
    phpdt_df_updated.to_csv(DAILY_FILENAME, index=False)
    print(f"PHPDT update complete for {current_date}: {phpdt_stats}")
