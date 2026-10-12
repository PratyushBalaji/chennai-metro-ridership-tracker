"""Record CMRL responses for offline collector tests."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests


BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", choices=("0", "1"), default="1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [
        f"{dashboard}/{endpoint}/{args.day}"
        for dashboard in ("PassengerFlow", "parkingdashboard")
        for endpoint in ("allTicketCount", "hourlybaseddata", "stationData")
    ]
    paths.append("PassengerFlow/PHPDTreport/")
    responses = {}
    for path in paths:
        url = BASE_URL + path
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        responses[url] = response.json()
        print(f"recorded {path}")
    bundle = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "day": args.day,
        "responses": responses,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
