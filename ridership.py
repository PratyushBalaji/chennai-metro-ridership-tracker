import requests
import pandas as pd

from monotonic_updates import read_csv_or_empty, upsert_monotonic

DAILY_FILENAME = "Ridership/ChennaiMetro_Daily_Ridership.csv"
HOURLY_FILENAME = "Ridership/ChennaiMetro_Hourly_Ridership.csv"
STATION_FILENAME = "Ridership/ChennaiMetro_Station_Ridership.csv"

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"
DAY = "1"

DAILY_TICKET_COUNT_URL = BASE_URL + "allTicketCount/" + DAY
HOURLY_PASSENGER_DATA_URL = BASE_URL + "hourlybaseddata/" + DAY
STATION_FLOW_DATA_URL = BASE_URL + "stationData/" + DAY

STATION_CODES = {
    "WIMCO NAGAR DEPOT": "SWD",
    "WIMCO NAGAR METRO": "SWN",
    "THIRUVOTRIYUR METRO": "STV",
    "THIRUVOTRIYUR THERADI METRO": "STT",
    "KALADIPET METRO": "SKP",
    "TOLLGATE METRO": "STG",
    "NEW WASHERMENPET METRO": "SNW",
    "TONDIARPET METRO": "STR",
    "THIYAGARAYA COLLEGE METRO": "STC",
    "WASHERMANPET": "SWA",
    "MANNADI": "SMA",
    "HIGH COURT": "SHC",
    "GOVERNMENT ESTATE": "SGE",
    "LIC": "SLI",
    "THOUSAND LIGHT": "STL",
    "AG-DMS": "SGM",
    "TEYNAMPET": "STE",
    "NANDANAM": "SCR",
    "SAIDAPET": "SSA",
    "LITTLE MOUNT": "SLM",
    "GUINDY": "SGU",
    "OTA - NANGANALLUR ROAD": "SOT",
    "MEENAMBAKKAM": "SME",
    "CHENNAI AIRPORT": "SAP",
    "EGMORE": "SEG",
    "NEHRU PARK": "SNP",
    "KILPAUK": "SKM",
    "PACHAIAPPA S COLLEGE": "SPC",
    "SHENOY NAGAR": "SSN",
    "ANNA NAGAR EAST": "SAE",
    "ANNA NAGAR TOWER": "SAT",
    "THIRUMANGALAM": "STI",
    "KOYAMBEDU": "SKO",
    "CMBT": "SCM",
    "ARUMBAKKAM": "SAR",
    "VADAPALANI": "SVA",
    "ASHOK NAGAR": "SAN",
    "EKKATTUTHANGAL": "SSI",
    "St. THOMAS MOUNT": "SMM",
    "CENTRAL  METRO": "SCC",
    "ALANDUR": "SAL",
}


def convert_station_code(station_name):
    return STATION_CODES.get(station_name, station_name)


def get_daily_ticket_count():
    return requests.get(DAILY_TICKET_COUNT_URL).json()


def get_hourly_passenger_data():
    response = requests.get(HOURLY_PASSENGER_DATA_URL).json()
    return {
        "date": pd.to_datetime(response["categories"][0]).strftime("%Y-%m-%d"),
        "timestamps": [pd.to_datetime(ts).strftime("%H:%M") for ts in response["categories"]],
        "series": {s["name"]: s["data"] for s in response["series"]},
    }


def get_station_flow_data():
    response = requests.get(STATION_FLOW_DATA_URL).json()
    station_data = {}
    for line_data in response:
        line_number = line_data["line"]
        station_data[line_number] = {
            "stations": list(map(convert_station_code, line_data["categories"])),
            "series": {s["name"]: s["data"] for s in line_data["series"]},
        }
    return station_data


daily_tickets = get_daily_ticket_count()
hourly_passengers = get_hourly_passenger_data()
station_flows = get_station_flow_data()
DATE = hourly_passengers["date"]

# DAILY
daily_payment_methods = sorted([key for key in daily_tickets.keys() if key.startswith("noOf")])
daily_row = {"Date": DATE, "Total": daily_tickets["totalTickets"]}
for method in daily_payment_methods:
    daily_row[method] = daily_tickets[method]
daily_df_new = pd.DataFrame([daily_row])
daily_df_existing = read_csv_or_empty(DAILY_FILENAME)
daily_df_updated, daily_stats = upsert_monotonic(
    existing_df=daily_df_existing,
    new_df=daily_df_new,
    key_cols=["Date"],
    dataset_name="ridership-daily",
)
daily_df_updated.to_csv(DAILY_FILENAME, index=False)
print(f"Daily ridership update complete for {DATE}: {daily_stats}")

# HOURLY
hourly_payment_methods = sorted([key for key in hourly_passengers["series"].keys() if key != "Total"])
hourly_rows = []
for i, timestamp in enumerate(hourly_passengers["timestamps"]):
    row = {
        "Date": DATE,
        "Hour": timestamp,
        "Total": hourly_passengers["series"]["Total"][i],
    }
    for method in hourly_payment_methods:
        row[method] = hourly_passengers["series"][method][i]
    hourly_rows.append(row)
hourly_df_new = pd.DataFrame(hourly_rows)
hourly_df_existing = read_csv_or_empty(HOURLY_FILENAME)
hourly_df_updated, hourly_stats = upsert_monotonic(
    existing_df=hourly_df_existing,
    new_df=hourly_df_new,
    key_cols=["Date", "Hour"],
    dataset_name="ridership-hourly",
)
hourly_df_updated.to_csv(HOURLY_FILENAME, index=False)
print(f"Hourly ridership update complete for {DATE}: {hourly_stats}")

# STATIONWISE
first_line_key = list(station_flows.keys())[0]
station_payment_methods = sorted([key for key in station_flows[first_line_key]["series"].keys() if key != "Total"])
station_rows = []
for line_number, line_data in station_flows.items():
    stations = line_data["stations"]
    for i, station_code in enumerate(stations):
        row = {
            "Date": DATE,
            "Line": line_number,
            "Station": station_code,
            "Total": line_data["series"]["Total"][i],
        }
        for method in station_payment_methods:
            row[method] = line_data["series"][method][i]
        station_rows.append(row)
station_df_new = pd.DataFrame(station_rows)
station_df_existing = read_csv_or_empty(STATION_FILENAME)
station_df_updated, station_stats = upsert_monotonic(
    existing_df=station_df_existing,
    new_df=station_df_new,
    key_cols=["Date", "Line", "Station"],
    dataset_name="ridership-stationwise",
)
station_df_updated.to_csv(STATION_FILENAME, index=False)
print(f"Station ridership update complete for {DATE}: {station_stats}")
