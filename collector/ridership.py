from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from time import sleep
from uuid import uuid4

import pandas as pd

from collector import fetch_json

from collector.anomalies import (
    ANOMALY_EXIT_CODE, ANOMALY_LOG_PATH, IST, RETRY_CUTOFF_IST,
    append_jsonl, append_retry_status, build_log_record, read_last_record, retry_decision,
)
from collector.schemas import (
    RIDERSHIP_DAILY, RIDERSHIP_HOURLY, RIDERSHIP_STATION, RIDERSHIP_STATIONS,
    RIDERSHIP_FARES, CORE_HOURS, LINE04_FROM, TVM_FROM, CARD_FIELDS,
    ONDC_FIELDS, LEGACY_CARD_FIELDS,
)
from collector.csv import read_csv, write_csv_group


DAILY_FILENAME = "ridership/ChennaiMetro_Daily_Ridership.csv"
HOURLY_FILENAME = "ridership/ChennaiMetro_Hourly_Ridership.csv"
STATION_FILENAME = "ridership/ChennaiMetro_Station_Ridership.csv"

BASE_URL = "https://commuters-dataapi.chennaimetrorail.org/api/PassengerFlow/"


class PayloadUnavailableError(ValueError):
    pass

STATION_CODES = {
    # Blue Line
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
    # Green Line
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
    # Interchange stations
    "CENTRAL  METRO": "SCC",
    "ALANDUR": "SAL",
    # Yellow Line (corridor 4)
    "POONAMALLEE BYPASS METRO": "POB",
    "POONAMALLEE BUS STAND METRO": "POO",
    "POONAMALLEE GOVERNMENT HOSPITAL METRO": "MUL",
    "KARAYANCHAVADI METRO": "KAC",
    "KUMANANCHAVADI METRO": "KUC",
    "KATTUPAKKAM METRO": "KPM",
    "IYAPPANTHANGAL METRO": "IYP",
    "THELLIYARAGARAM METRO": "THL",
    "PORUR BYPASS METRO": "PBP",
    "PORUR JUNCTION METRO": "PRJ",
}


def endpoint(path: str, day: str) -> str:
    return f"{BASE_URL}{path}/{day}"


def convert_station_code(station_name: str) -> str:
    return STATION_CODES.get(station_name, station_name)


def fetch_ridership_payloads(day, api_response=None, capture_times=None):
    payloads = []
    for name in ("allTicketCount", "hourlybaseddata", "stationData"):
        url = endpoint(name, day)
        started = datetime.now(timezone.utc)
        payloads.append(fetch_json(url, api_response))
        if capture_times is not None:
            capture_times[url] = {"started_at_utc": started.isoformat(),
                                  "finished_at_utc": datetime.now(timezone.utc).isoformat()}
    return tuple(payloads)


def extract_dataset_date(hourly_response: dict[str, Any]) -> str:
    categories = hourly_response.get("categories") if isinstance(hourly_response, dict) else None
    if not categories or not categories[0]:
        raise PayloadUnavailableError("Hourly ridership response has no timestamp categories")
    return pd.to_datetime(categories[0]).strftime("%Y-%m-%d")


def normalize_daily(daily_tickets: dict[str, Any], date: str) -> pd.DataFrame:
    payment_methods = sorted(key for key in daily_tickets if key.startswith("noOf"))
    row = {
        "Date": date,
        "Total": daily_tickets["totalTickets"],
    }
    row.update({method: daily_tickets[method] for method in payment_methods})
    return pd.DataFrame([row], columns=["Date", "Total", *payment_methods])


def normalize_hourly(hourly_response: dict[str, Any]) -> pd.DataFrame:
    date = extract_dataset_date(hourly_response)
    timestamps = [pd.to_datetime(timestamp).strftime("%H:%M") for timestamp in hourly_response["categories"]]
    series = {entry["name"]: entry["data"] for entry in hourly_response["series"]}
    payment_methods = sorted(method for method in series if method != "Total")

    rows = []
    for index, timestamp in enumerate(timestamps):
        row = {
            "Date": date,
            "Hour": timestamp,
            "Total": series["Total"][index],
        }
        row.update({method: series[method][index] for method in payment_methods})
        rows.append(row)

    return pd.DataFrame(rows, columns=["Date", "Hour", "Total", *payment_methods])


def normalize_station(station_response: list[dict[str, Any]], date: str) -> pd.DataFrame:
    rows = []
    payment_methods: set[str] = set()

    for line_data in station_response:
        series = {entry["name"]: entry["data"] for entry in line_data["series"]}
        line_payment_methods = sorted(method for method in series if method != "Total")
        payment_methods.update(line_payment_methods)

        for index, station_name in enumerate(line_data["categories"]):
            row = {
                "Date": date,
                "Line": str(line_data["line"]).zfill(2),
                "Station": convert_station_code(station_name),
                "Total": series["Total"][index],
            }
            row.update({method: series[method][index] for method in line_payment_methods})
            rows.append(row)

    return pd.DataFrame(rows, columns=["Date", "Line", "Station", "Total", *sorted(payment_methods)], dtype=object)


@dataclass
class Snapshot:
    date: str
    day: str
    captured_at: datetime
    finished_at: datetime
    payloads: tuple
    findings: list[dict] = field(default_factory=list)
    frames: list[pd.DataFrame] = field(default_factory=list)
    response_times: dict = field(default_factory=dict)

    @property
    def complete(self):
        return not any(item["kind"] == "structural" for item in self.findings)

    @property
    def valid(self):
        return not self.findings


@dataclass
class SnapshotResult:
    date: str
    status: str
    published: bool
    exit_code: int
    findings: list[dict] = field(default_factory=list)
    revisions: list[dict] = field(default_factory=list)
    raw_paths: list[str] = field(default_factory=list)


def _count(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("expected a nonnegative whole count")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError("expected a nonnegative whole count")
        return int(number)
    except InvalidOperation as error:
        raise ValueError("expected a nonnegative whole count") from error


def validate_snapshot(snapshot, previous_hourly):
    """Check raw coverage before normalizers can hide duplicate or excess data."""
    findings = snapshot.findings

    def issue(kind, code, **details):
        findings.append({"kind": kind, "code": code, **details})

    def counts(data, where):
        result = []
        for index, value in enumerate(data):
            try:
                result.append(_count(value))
            except ValueError:
                issue("structural", "invalid_count", location=where, index=index, actual=repr(value))
                result.append(0)
        return result

    required_fares = {
        key: name for key, name in RIDERSHIP_FARES.items()
        if (key != "noOfUTPQR" or snapshot.date >= LINE04_FROM)
        and (key != "noOfTVM" or snapshot.date >= TVM_FROM)
    }

    def series(payload, where):
        if not isinstance(payload, dict) or not isinstance(payload.get("categories"), list) or not isinstance(payload.get("series"), list):
            issue("structural", "invalid_series_shape", location=where)
            return [], {}
        categories = payload["categories"]
        values = {}
        for entry in payload["series"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("data"), list):
                issue("structural", "invalid_series_shape", location=where)
                continue
            name, data = entry["name"], entry["data"]
            if name in values:
                issue("structural", "duplicate_series", location=where, column=name)
            if len(data) != len(categories):
                issue("structural", "series_length_mismatch", location=where, column=name,
                      expected=len(categories), actual=len(data))
            values[name] = counts(data, f"{where}/{name}")
        for name in {"Total", *required_fares.values()} - values.keys():
            issue("structural", "missing_fare", location=where, column=name)
        for name in values.keys() - {"Total", *RIDERSHIP_FARES.values()}:
            issue("arithmetic", "unmapped_fare", location=where, column=name)
        return categories, values

    daily, hourly, station = snapshot.payloads
    daily_counts = {}
    if not isinstance(daily, dict):
        issue("structural", "invalid_daily_shape")
    else:
        for name in {"totalTickets", "noOfTotal_QR", "noOfONDCQR", *required_fares} - daily.keys():
            issue("structural", "missing_fare", location="daily", column=name)
        for name, value in daily.items():
            if name == "totalTickets" or name.startswith("noOf"):
                daily_counts[name] = counts([value], f"daily/{name}")[0]
                if name not in {*RIDERSHIP_FARES, "totalTickets", "noOfTotal_QR", "noOfONDCQR"}:
                    if name not in LEGACY_CARD_FIELDS or daily_counts[name]:
                        issue("arithmetic", "unmapped_fare", location="daily", column=name)

    categories, hourly_values = series(hourly, "hourly")
    hours = []
    for raw in categories:
        try:
            if not isinstance(raw, str):
                raise ValueError("not a timestamp")
            # CMRL's Z-suffixed service-hour labels retain their archive wall time.
            stamp = pd.Timestamp(raw)
            if pd.isna(stamp) or stamp.strftime("%Y-%m-%d") != snapshot.date or stamp.minute or stamp.second or stamp.microsecond:
                raise ValueError("wrong date or not an hourly timestamp")
            hours.append(stamp.strftime("%H:%M"))
        except (ValueError, TypeError, OverflowError):
            issue("structural", "invalid_timestamp", actual=repr(raw))
    if len(hours) != len(set(hours)):
        issue("structural", "duplicate_hour")
    required_hours = set(CORE_HOURS)
    if snapshot.day == "0":
        boundary = snapshot.captured_at.astimezone(IST).strftime("%H:00")
        required_hours = {hour for hour in required_hours if hour < boundary}
    if not previous_hourly.empty:
        for _, row in previous_hourly.loc[previous_hourly["Date"] == snapshot.date].iterrows():
            if _count(row["Total"]) > 0:
                required_hours.add(row["Hour"])
    if not categories:
        issue("structural", "empty_hourly")
    missing_hours = sorted(required_hours - set(hours))
    if missing_hours:
        issue("structural", "missing_hours", missing=missing_hours)

    expected = {(line, code) for line, codes in RIDERSHIP_STATIONS.items()
                if line != "04" or snapshot.date >= LINE04_FROM for code in codes}
    actual, station_series, seen_lines = [], [], set()
    if not isinstance(station, list):
        issue("structural", "invalid_station_shape")
    else:
        for line in station:
            names, values = series(line, "station")
            number = str(line.get("line", "")).zfill(2) if isinstance(line, dict) else ""
            if number in seen_lines:
                issue("structural", "duplicate_line", line=number)
            if number not in {key[0] for key in expected}:
                issue("structural", "unexpected_line", line=number)
            seen_lines.add(number)
            for name in names:
                if not isinstance(name, str):
                    issue("structural", "invalid_station", actual=repr(name))
                    continue
                actual.append((number, convert_station_code(name)))
            station_series.append(values)
        if len(actual) != len(set(actual)):
            issue("structural", "duplicate_station")
        if set(actual) != expected:
            issue("structural", "station_coverage",
                  missing=sorted(expected - set(actual)), unexpected=sorted(set(actual) - expected))
    if not snapshot.complete:
        return findings

    def equal(code, expected_value, actual_value, **details):
        if expected_value != actual_value:
            issue("arithmetic", code, expected=expected_value, actual=actual_value, **details)

    for location, values in [("hourly", hourly_values), *[(f"station/{i}", v) for i, v in enumerate(station_series)]]:
        for index, total in enumerate(values["Total"]):
            equal("row_total", sum(data[index] for name, data in values.items() if name != "Total"),
                  total, location=location, index=index)
    qr = sum(value for name, value in daily_counts.items() if name in RIDERSHIP_FARES and name not in CARD_FIELDS)
    ondc = sum(daily_counts.get(name, 0) for name in ONDC_FIELDS)
    equal("qr_subtotal", qr, daily_counts["noOfTotal_QR"])
    equal("ondc_subtotal", ondc, daily_counts["noOfONDCQR"])
    equal("daily_components", daily_counts["noOfTotal_QR"] + sum(daily_counts.get(name, 0) for name in CARD_FIELDS),
          daily_counts["totalTickets"])
    equal("daily_hourly_total", daily_counts["totalTickets"], sum(hourly_values["Total"]))
    equal("daily_station_total", daily_counts["totalTickets"], sum(sum(v["Total"]) for v in station_series))
    return findings


def _utc_stamp(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("capture timestamp must include a timezone")
    return stamp.astimezone(timezone.utc)


def _capture(day, api_response, existing, expected_date):
    started = datetime.now(timezone.utc)
    metadata = {}
    if api_response is not None:
        with Path(api_response).open(encoding="utf-8") as file:
            metadata = json.load(file)
    times = {}
    payloads = fetch_ridership_payloads(day, api_response, times)
    finished = datetime.now(timezone.utc)
    capture_problem = None
    if api_response is not None:
        try:
            started = _utc_stamp(metadata["recorded_at_utc"])
            finished = _utc_stamp(metadata.get("finished_at_utc", metadata["recorded_at_utc"]))
            times = metadata.get("response_timestamps", {})
        except (KeyError, TypeError, ValueError, AttributeError):
            capture_problem = "missing_or_invalid_capture_timestamp"
    date = expected_date or (started.astimezone(IST).date() - timedelta(days=int(day))).isoformat()
    snapshot = Snapshot(date, day, started, finished, payloads, response_times=times)
    if date != (started.astimezone(IST).date() - timedelta(days=int(day))).isoformat():
        snapshot.findings.append({"kind": "structural", "code": "selector_date_mismatch"})
    if capture_problem:
        snapshot.findings.append({"kind": "structural", "code": capture_problem})
    if finished < started or started.astimezone(IST).date() != finished.astimezone(IST).date():
        snapshot.findings.append({"kind": "structural", "code": "capture_day_rollover"})
    if times:
        try:
            for name in ("allTicketCount", "hourlybaseddata", "stationData"):
                timing = times[endpoint(name, day)]
                start, end = (_utc_stamp(timing[key]) for key in ("started_at_utc", "finished_at_utc"))
                if not started <= start <= end <= finished:
                    raise ValueError("endpoint capture is outside the attempt")
        except (KeyError, TypeError, ValueError, AttributeError):
            snapshot.findings.append({"kind": "structural", "code": "invalid_response_timestamps"})
    validate_snapshot(snapshot, existing[1])
    if snapshot.complete:
        daily, hourly, station = payloads
        snapshot.frames = [normalize_daily(daily, date), normalize_hourly(hourly), normalize_station(station, date)]
        for frame, schema in zip(snapshot.frames, SCHEMAS):
            for column in frame.columns.difference(list(schema.key_columns)):
                frame[column] = frame[column].map(lambda value: _count(value) if pd.notna(value) else None).astype(object)
        for old, frame, schema in zip(existing, snapshot.frames, SCHEMAS):
            if old.empty:
                continue
            incoming = {tuple(row[k] for k in schema.key_columns): row for row in frame.to_dict("records")}
            for row in old.loc[old["Date"] == date].to_dict("records"):
                key = tuple(row[k] for k in schema.key_columns)
                if key not in incoming:
                    continue
                for column, value in row.items():
                    if column in schema.key_columns or pd.isna(value) or value == "":
                        continue
                    if (_count(value) == 0 and
                            ((column in ("noOfUTPQR", "UTP QR") and date < LINE04_FROM)
                             or (column in ("noOfTVM", "TVM") and date < TVM_FROM))):
                        continue
                    if column not in incoming[key] or pd.isna(incoming[key][column]):
                        snapshot.findings.append({"kind": "structural", "code": "missing_fare",
                                                  "location": schema.name, "key": key, "column": column})
    return snapshot


SCHEMAS = (RIDERSHIP_DAILY, RIDERSHIP_HOURLY, RIDERSHIP_STATION)
FILENAMES = (DAILY_FILENAME, HOURLY_FILENAME, STATION_FILENAME)
PUBLICATION_STATUSES = ("published_provisional", "published_final")


def _replace_date(existing, incoming, date, zero_column):
    existing, incoming = existing.copy(), incoming.copy()
    if not existing.empty and zero_column in incoming and zero_column not in existing:
        existing[zero_column] = 0
    if date < LINE04_FROM and zero_column in existing and zero_column not in incoming:
        incoming[zero_column] = 0
    if not existing.empty:
        existing = existing.loc[existing["Date"] != date]
    columns = list(existing.columns) + [c for c in incoming if c not in existing]
    return pd.concat([existing.astype(object), incoming.astype(object)], ignore_index=True).reindex(
        columns=columns).sort_values("Date", kind="stable").reset_index(drop=True)


def _revisions(existing, incoming, schema):
    if existing.empty:
        return []
    index = {tuple(row[k] for k in schema.key_columns): row for row in existing.to_dict("records")}
    changes = []
    for row in incoming.to_dict("records"):
        key = {k: row[k] for k in schema.key_columns}
        old = index.get(tuple(key.values()))
        if old is None:
            continue
        details = []
        for column in incoming.columns.difference(list(schema.key_columns)):
            if column not in old or pd.isna(old[column]) or old[column] == "" or pd.isna(row[column]):
                continue
            if _count(row[column]) != _count(old[column]):
                details.append({"column": column, "existing": old[column], "incoming": row[column],
                                "reason": "incoming_value_decreased" if _count(row[column]) < _count(old[column])
                                          else "incoming_value_increased"})
        if details:
            changes.append({"dataset": schema.name, "key": key, "action": "revised", "details": details})
    return changes


def _archive_snapshot(snapshot, output_dir, run_id, attempt):
    path = Path(output_dir) / "discrepancies/payloads/ridership" / snapshot.date / f"{run_id}-{attempt}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "recorded_at_utc": None if any(f["code"] == "missing_or_invalid_capture_timestamp"
                                        for f in snapshot.findings) else snapshot.captured_at.isoformat(),
        "finished_at_utc": snapshot.finished_at.isoformat(),
        "response_timestamps": snapshot.response_times,
        "day": snapshot.day, "date": snapshot.date, "validation": snapshot.findings,
        "responses": {endpoint(name, snapshot.day): payload for name, payload in zip(
            ("allTicketCount", "hourlybaseddata", "stationData"), snapshot.payloads)},
    }
    with path.open("x", encoding="utf-8") as file:
        json.dump(bundle, file, ensure_ascii=True, indent=2)
        file.write("\n")
    return path.relative_to(output_dir).as_posix()


def collect_ridership(day="1", output_dir="data", dry_run=False, anomaly_log=ANOMALY_LOG_PATH,
                      api_response=None, expected_date=None):
    paths = [Path(output_dir) / name for name in FILENAMES]
    existing = [read_csv(path) for path in paths]
    log_path = _resolve_output_path(output_dir, anomaly_log)
    attempts = [_capture(day, api_response, existing, expected_date)]
    first = attempts[0]
    previous = read_last_record(log_path, source="ridership", dataset_date=first.date, statuses=PUBLICATION_STATUSES)
    if not dry_run and previous and (day == "0" and previous["status"] == "published_final"
                     or first.captured_at < _utc_stamp(previous["captured_at_utc"])):
        return SnapshotResult(first.date, "unchanged", False, 0)

    if not first.valid and not dry_run:
        # This first retry does not depend on GitHub delivering its next cron.
        now = first.finished_at if api_response is not None else datetime.now(timezone.utc)
        def can_retry(stamp):
            local = stamp.astimezone(IST)
            return ((local.date() - timedelta(days=int(day))).isoformat() == first.date
                    and (day == "1" or local.time().replace(tzinfo=None) < RETRY_CUTOFF_IST))

        later = now + timedelta(seconds=60)
        if can_retry(later):
            sleep(60)
            now = later if api_response is not None else datetime.now(timezone.utc)
            if can_retry(now):
                retry = _capture(day, api_response, existing, first.date)
                if previous and retry.captured_at < _utc_stamp(previous["captured_at_utc"]):
                    retry.findings.append({"kind": "structural", "code": "stale_capture"})
                attempts.append(retry)

    selected = next((item for item in reversed(attempts) if item.valid), None)
    if selected is None and day == "0" and not dry_run:
        selected = next((item for item in reversed(attempts) if item.complete), None)
    findings = (selected or attempts[-1]).findings
    revisions, outputs = [], []
    if selected is not None:
        for path, old, frame, schema in zip(paths, existing, selected.frames, SCHEMAS):
            revisions.extend(_revisions(old, frame, schema))
            zero = "noOfUTPQR" if schema == RIDERSHIP_DAILY else "UTP QR"
            outputs.append((path, _replace_date(old, frame, first.date, zero)))

    status = ("published_final" if day == "1" else "published_provisional") if selected else (
        "pending_retry" if day == "0" else "needs_review")
    code = 0 if selected and selected.valid else ANOMALY_EXIT_CODE
    changed = bool(outputs) and any(not old.fillna("").astype(str).equals(frame.fillna("").astype(str))
                                   for old, (_, frame) in zip(existing, outputs))
    promotion = selected is not None and (previous is None or previous["status"] != status
                                          or previous.get("validation_passed") != selected.valid
                                          or selected.captured_at > _utc_stamp(previous["captured_at_utc"]))
    result = SnapshotResult(first.date, status, bool(selected and (changed or promotion) and not dry_run), code, findings, revisions)
    if dry_run:
        print(f"Dry run: {first.date}, validation {'passed' if first.valid else 'failed'}")
        return result

    reductions = any(detail["reason"] == "incoming_value_decreased"
                     for revision in revisions for detail in revision["details"])
    if not first.valid or reductions:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
        result.raw_paths = [_archive_snapshot(item, output_dir, run_id, i) for i, item in enumerate(attempts, 1)]
    record = None
    if changed or promotion or not selected or findings or revisions:
        record = build_log_record(source="ridership", day=day, dataset_date=first.date, status=status,
                                  reason="snapshot_validation" if findings else "snapshot_publication",
                                  anomalies=revisions)
        record.update({
            "published": selected is not None, "validation_passed": bool(selected and selected.valid),
            "captured_at_utc": (selected or attempts[-1]).captured_at.isoformat(),
            "selected_attempt": next((i for i, item in enumerate(attempts, 1) if item is selected), None),
            "findings": findings, "payload_paths": result.raw_paths,
            "attempts": [{"captured_at_utc": item.captured_at.isoformat(), "findings": item.findings} for item in attempts],
        })
    if changed:
        write_csv_group(outputs, after_write=lambda: append_jsonl(log_path, record))
    elif record is not None:
        append_jsonl(log_path, record)
    print(f"ridership {first.date}: {status}, {len(findings)} findings, {len(revisions)} revised rows")
    return result


def _resolve_output_path(output_dir, path):
    path = Path(path)
    return path if path.is_absolute() else Path(output_dir) / path


def parse_args():
    parser = argparse.ArgumentParser(description="Collect Chennai Metro ridership data.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--day", default="1", choices=("0", "1"), help="CMRL day selector: 0=today, 1=yesterday")
    mode.add_argument("--retry", action="store_true", help="Retry a held live snapshot when due")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--api-response", type=Path, help="Read a recorded response bundle instead of HTTP")
    parser.add_argument("--dry-run", action="store_true", help="Validate one attempt without sleeping or writing")
    parser.add_argument("--strict", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--anomaly-log", default=ANOMALY_LOG_PATH)
    return parser.parse_args()


def retry_ridership(args):
    log_path = _resolve_output_path(args.output_dir, args.anomaly_log)
    previous = read_last_record(log_path, source="ridership", day="0")
    if previous and previous.get("status") == "pending_retry":
        final = read_last_record(log_path, source="ridership", dataset_date=previous["date"], statuses=("published_final",))
        if final:
            if not args.dry_run:
                append_retry_status(log_path, previous, "resolved", "final_snapshot_accepted")
            return 0
    now = None
    if args.api_response is not None:
        with Path(args.api_response).open(encoding="utf-8") as file:
            now = _utc_stamp(json.load(file)["recorded_at_utc"])
    decision = retry_decision(previous, now)
    print(f"retry: {decision}")
    if decision in ("skip", "wait"):
        return 0
    if decision == "expired":
        if not args.dry_run:
            append_retry_status(log_path, previous, "expired", "retry_cutoff")
        return ANOMALY_EXIT_CODE
    result = collect_ridership(day="0", output_dir=args.output_dir, dry_run=args.dry_run,
                              anomaly_log=args.anomaly_log, api_response=args.api_response,
                              expected_date=previous["date"])
    if result.exit_code == 0 and not args.dry_run:
        append_retry_status(log_path, previous, "resolved", "retry_ok")
    return result.exit_code


def main():
    args = parse_args()
    code = retry_ridership(args) if args.retry else collect_ridership(
        day=args.day, output_dir=args.output_dir, dry_run=args.dry_run,
        anomaly_log=args.anomaly_log, api_response=args.api_response).exit_code
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
