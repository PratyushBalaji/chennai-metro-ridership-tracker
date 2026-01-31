# Chennai Metro Ridership Tracker

Python-based scripts to build and maintain a dataset of Chennai Metro's ridership through time. Includes aggregate daily, hourly, and stationwise passenger flow statistics.

All data in this repository is scraped from publicly provided data by Chennai Metro Rail Limited. Their dashboards only contain data for upto one day in the past, so this repository serves to archive that data through time.

Inspired by a similar project for Bengaluru's Namma Metro : https://github.com/thecont1/namma-metro-ridership-tracker

Temporarily, I will be using an internal (but publicly callable) CMRL API for data collection found through a simple network traffic analysis on CMRL's [official Passenger Flow Dashboard](https://commuters-data.chennaimetrorail.org/passengerflow).

However, in the near future, I will replace this with a selenium (or alternative browser automation tool) based control flow that will scrape the same data from the website as if it were a real human tediously copying numbers from the dashboard, as the original namma metro ridership tracker does. I will soon also be using GitHub Actions (CI/CD) to automate the data scraping daily instead of manually making commits (as the original project also did).

## Disclaimer

This project is intended **strictly** for **research, educational and informational purposes**. The data and API usage demonstrated here and in related repositories are meant solely to help understand how CMRL metro services function and retrieve data. Any use of this information in personal or public applications may place unintended load on CMRL servers and disrupt official services.

**I do not accept any responsibility or liability for consequences arising from the misuse of this data or API. Use at your own discretion and respect the intended limits of the service.**

## General Information

The [ridership.py](./ridership.py), [parking.py](./parking.py) and [phpdt.py](./phpdt.py) files perform the data scraping, basic pre-processing, and daily data append to the actual CSVs. These are what will be used to maintain the dataset. (Requirements : `os`, `pandas`, `requests`)

The CSV files contain the actual historical [daily](./ChennaiMetro_Daily_Ridership.csv), [hourly](./ChennaiMetro_Hourly_Ridership.csv), and [stationwise](./ChennaiMetro_Station_Ridership.csv) ridership, parking and PHPDT data. The collection was started on January 24th 2026.

The project directory also contains [a Jupyter Notebook](./ChennaiMetroDataViz.ipynb) that takes you through how to recreate the graphs on the official CMRL dashboard using the data being collected. Use the [requirements file](requirements.txt) to install any Python modules required in the notebook through pip.

The [CMRL API](./CMRL%20API/) folder contains a Bruno collection that was used for the API testing. You can use it to hit the API endpoints and view the JSON response, incase you want to fetch the data directly from the source or mess with the API. This collection can also be exported to Postman, as cURL requests, etc, for your convenience.

Currently, the repository collates : 
- Ridership Data (Daily, Hourly, Stationwise) broken down by fare payment method
- Parking Data (Daily, Hourly, Stationwise) broken down by vehicle type
- Peak Hour Per Direction Traffic (PHPDT) ridership data (Daily) for each adjacent station pair

As the primary intent is **ridership data collection**, despite other data being collected in this repository, the docs and programs often reference the ridership dataset and scripts. Most (if not all) of the information regarding data storage, script functionality, etc, is applicable across datasets and scripts, and the codebase is intended to be both self-documenting and consistent.

## To-do List
- [ ] Data validation and error-handling logic
- [ ] Replace direct API calls with `selenium` (or alternative browser automation tool) based control flow
- [ ] Finalise data storage schema
- [x] Automate data scraping using GitHub Actions (CI/CD)

## Data Structure

Currently, I'm contemplating the best way to store the data. The goal is to record the information in a manner that is both easy to understand and manipulate for a novice programmer, but also convenient for rigorous and robust data analyses by more experienced persons.

### Current Design

Each CSV file contains these generic columns, with some particulars depending on the file.

**Global Columns :** Date, Total, \<All payment modes\> (Stored Value Card, NCMC Card, Whatsapp QR, etc) - Payment modes are sorted alphabetically for consistency

**Specific Columns :**

- Hourly : Hour (HH:MM format)
- Station-wise : Line (01|02), Station (3-letter Unique Station Code)

### Potential Alternatives

The current data format is perfectly suitable for the daily aggregate appending, but not the best for the two other datasets. This is because while the daily append is 2-dimensional (Date, Pax / Mode of fare payment), the other datasets have a third variable (Hour of day | Station).

This complicates things a bit since one day's data takes numerous columns to store in a CSV, one for each potential value of the third variable. ~24 rows for each hour of the day in the hourly dataset, and ~43 rows for each station in the station-wise dataset (Central (SCC) and Alandur (SAL) are repeated twice since the passenger count distinguishes between metro lines.)

I am looking into alternative formats, however for the intended usage of this data largely for research, analysis, and visualisation, CSV is more than appropriate and any cons it may have are but minor hindrances. If there is another data format that satisfies all my criteria (comprehension at-a-glance, usable regardless of expertise, etc), I will use it, but this version will always be usable too.

## License
This project is licensed under the MIT License - see the LICENSE file for details.
