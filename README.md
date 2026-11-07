# Chennai Metro Ridership Tracker

Python-based scripts to build and maintain a dataset of Chennai Metro's ridership through time. Includes aggregate daily, hourly, and stationwise passenger flow statistics.

All data in this repository is scraped from publicly provided data by Chennai Metro Rail Limited. Their dashboards only contain data for upto one day in the past, so this repository serves to archive that data through time.

Inspired by a similar project for Bengaluru's Namma Metro : https://github.com/thecont1/namma-metro-ridership-tracker

Temporarily, I will be using an internal (but publicly callable) CMRL API for data collection found through a simple network traffic analysis on CMRL's [official Passenger Flow Dashboard](https://commuters-data.chennaimetrorail.org/passengerflow).

However, in the near future, I will replace this with a selenium (or alternative browser automation tool) based control flow that will scrape the same data from the website as if it were a real human tediously copying numbers from the dashboard, as the original namma metro ridership tracker does.

**Contributions are welcome! Feel free to fork, raise issues, or start discussions**

I am also working on a sister project to visualise this data and extend the target audience from devs and researchers to everyday metro commuters! Check it out : https://github.com/PratyushBalaji/chennai-metro-ridership-visualisation

## Data notice / third-party content

This project is intended **strictly** for **research, educational and informational purposes**. The data and API usage demonstrated here and in related repositories are meant solely to help understand how CMRL metro services function and retrieve data. Any use of this information in personal or public applications may place unintended load on CMRL servers and disrupt official services.

**I do not accept any responsibility or liability for consequences arising from the misuse of this data or API. Use at your own discretion and respect the intended limits of the service.**

This repository includes scripts and workflows to collect and archive daily passenger flow data published by Chennai Metro Rail Limited (CMRL) at :
https://commuters-data.chennaimetrorail.org/passengerflow and other related sites. The data is lawfully copyrighted by **Chennai Metro Rail Limited**.

Accordingly:
- The MIT License in this repository applies solely to the code in this repo (scripts, notebooks, etc.).
- It does **not** grant any rights to the underlying CMRL-published data (including any CSV exports, archived snapshots, or reproduced tables), which are be subject to CMRL’s copyright and terms.
- If you wish to reuse or redistribute the data, you are responsible for ensuring you have the necessary permissions from CMRL or that your use is otherwise lawful.

## General Information

The [ridership.py](./collector/ridership.py), [parking.py](./collector/parking.py) and [phpdt.py](./collector/phpdt.py) files perform the data scraping, basic pre-processing, and CSV upserts. These are what will be used to maintain the dataset. (Requirements : `os`, `pandas`, `requests`)

Ridership and parking only update a row if all populated values stay equal or increase; blank values can be filled. Mixed/lower rows are kept unchanged and flagged. PHPDT only inserts missing corridors or fills blanks; changes to populated counts or hours need review.

Anomalies are appended to `data/discrepancies/anomaly_log.jsonl` with UTC/IST timestamps. Exit code `0` means success, `2` means anomalies (other valid rows may still be saved), and `1` means a collection error. See [tests](./tests/README.md) for fixture testing and retry behaviour.

The CSV files contain the actual historical [daily](./data/ridership/ChennaiMetro_Daily_Ridership.csv), [hourly](./data/ridership/ChennaiMetro_Hourly_Ridership.csv), and [stationwise](./data/ridership/ChennaiMetro_Station_Ridership.csv) ridership, parking and PHPDT data. The collection was started on January 24th 2026.

The project directory also contains [a Jupyter Notebook](./notebooks/ChennaiMetroDataViz.ipynb) that takes you through how to recreate the graphs on the official CMRL dashboard using the data being collected. Use `pip install -e ".[notebook]"` to install any Python modules required in the notebook through pip.

The [tools/bruno](./tools/bruno/) folder contains a Bruno collection that was used for the API testing. You can use it to hit the API endpoints and view the JSON response, incase you want to fetch the data directly from the source or mess with the API. This collection can also be exported to Postman, as cURL requests, etc, for your convenience.

Currently, the repository collates : 
- Ridership Data (Daily, Hourly, Stationwise) broken down by fare payment method
- Parking Data (Daily, Hourly, Stationwise) broken down by vehicle type
- Peak Hour Per Direction Traffic (PHPDT) ridership data (Daily) for each adjacent station pair

As the primary intent is **ridership data collection**, despite other data being collected in this repository, the docs and programs often reference the ridership dataset and scripts. Most (if not all) of the information regarding data storage, script functionality, etc, is applicable across datasets and scripts, and the codebase is intended to be both self-documenting and consistent.

## To-do List
- [ ] Data validation and error-handling logic
- [ ] Replace direct API calls with `selenium` (or alternative browser automation tool) based control flow
- [ ] Generalised algorithm to future-proof scripts as new lines / stations open
- [x] Finalise data storage schema
- [x] Automate data scraping using GitHub Actions (CI/CD)

## Data Structure

Currently, I'm contemplating the best way to store the data. The goal is to record the information in a manner that is both easy to understand and manipulate for a novice programmer, but also convenient for rigorous and robust data analyses by more experienced persons.

### Current Design

Each CSV file contains these generic columns, with some particulars depending on the file.

**Global Columns :** Date, Total, \<All payment modes\> (Stored Value Card, NCMC Card, Whatsapp QR, etc) - Payment modes are sorted alphabetically for consistency

**Specific Columns :**

- Hourly : Hour (HH:MM format)
- Station-wise : Line (01|02|04), Station (station code)

Lines 01 and 02 use the official station codes as on the CMRL website and Wikipedia, The new line 04 uses the CMRL PHPDT codes POB, POO, MUL, KAC, KUC, KPM, IYP, THL, PBP, PRJ, and SVA in route order as their official codes are not public knowledge yet. Interchange stations retain one code across lines but are tagged with their line number as opposed to how they are internally displayed (SVA, SVA4, SAL_C1, SAL_C2, etc)

UTP is stored as `noOfUTPQR` in daily ridership and `UTP QR` in hourly/station data.
Older UTP values have been backfilled with zero as the fare type was not previously in use.

Parking station CSVs use `Eight Wheeler` only; the old `Eight Wheleer` \[sic\] header is coalesced into it, with conflicting populated values rejected.

PHPDT includes line 04 when its route arrays have data. Poonamallee to Vadapalani is labelled UP, the reverse DOWN. API suffixes such as SVA4, SCC-C2 and SAL-C2 normalise to SVA, SCC and SAL.

### Potential Alternatives

The current data format is perfectly suitable for the daily aggregate appending, but not the best for the two other datasets. This is because while the daily append is 2-dimensional (Date, Pax / Mode of fare payment), the other datasets have a third variable (Hour of day | Station).

This complicates things a bit since one day's data takes numerous columns to store in a CSV, one for each potential value of the third variable. ~24 rows for each hour of the day in the hourly dataset, and ~43 rows for each station in the station-wise dataset (Central (SCC) and Alandur (SAL) are repeated twice since the passenger count distinguishes between metro lines.)

I am looking into alternative formats, however for the intended usage of this data largely for research, analysis, and visualisation, CSV is more than appropriate and any cons it may have are but minor hindrances. If there is another data format that satisfies all my criteria (comprehension at-a-glance, usable regardless of expertise, etc), I will use it, but this version will always be usable too.

## License
This project and its Author's contributions are licensed under the MIT License - see the LICENSE file for details. This license does **not** grant any additional rights to the underlying data which are subject to CMRL's copyright terms.
