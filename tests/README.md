# collector benchmark

These tests execute the current `collector.ridership`, `collector.parking` and `collector.phpdt` modules.
Original expected CSVs were captured from main at `991a0a4`. Ridership's
normalization still matches them; publication now follows snapshot validation.

```sh
pip install -e ".[test]"
python -m pytest
```

The suite uses [pytest](https://docs.pytest.org/),
[responses](https://github.com/getsentry/responses) to mock requests,
and [pytest-socket](https://github.com/miketheman/pytest-socket) to block network
access. Each test writes to pytest's temporary directory.

`fixtures/api/day1.json` contains fixed CMRL responses captured on
2026-10-12 UTC. They describe data for 2026-10-11. No live calls happen in CI.
The seven CSVs in `fixtures/expected/` were produced by the original main
scripts against those responses. Comparisons check columns, values and row
counts; rows are sorted for comparison so file ordering is not part of the
contract.

`fixtures/seed/` has fake entries dated 2000-01-01 and 2099-12-31. Tests use
these to cover incoming dates before, on and after the latest stored date.
Existing rows must survive and a repeat of the latest payload must not add
duplicates. The benchmark also checks empty CSVs and parking's `DATASET_DATE`
override.

All three collectors accept fixed responses directly from the command line:

```sh
python -m collector.ridership --api-response tests/fixtures/api/consistent_ridership.json --output-dir test-output
python -m collector.parking --api-response tests/fixtures/api/day1.json --output-dir test-output
python -m collector.phpdt --api-response tests/fixtures/api/day1.json --output-dir test-output
```

This writes CSVs under `test-output/ridership/`, `test-output/parking/` and
`test-output/phpdt/`. Without `--api-response`, the scripts call the live API.
Without `--output-dir`, they use the output folders under `data/`. The CLI tests run
the collector modules with these flags, compare the CSVs and verify that no HTTP
calls happened. Parking's date fallback also reads from the JSON file;
`DATASET_DATE` still takes precedence when supplied.

The original benchmark did not cover snapshot acceptance. Those decisions are
covered separately below. The original scripts only skip when the API date
equals the last CSV row.

To explicitly record a replacement response bundle:

```sh
python tools/record_api_responses.py --day 1 --output tests/fixtures/api/day1.json
```

Review changes to the JSON and expected CSVs together. Tests never regenerate
expected CSVs automatically.

GitHub Actions first runs each collector module with `--api-response` and
`--output-dir`, then runs pytest on main, dev branches and PRs. The generated
CSVs, JUnit results and coverage report (under `.cache/reports/`) are uploaded.
This is a regression suite, not a guarantee about future CMRL availability.

## ridership snapshots

```sh
python -m pytest -m idempotence
```

`fixtures/api/consistent_ridership.json` is explicitly synthetic: 54 station/line
pairs each have five card entries, giving 270 passengers, all assigned to 06:00.
Reviewed expected CSVs live under `fixtures/snapshot_expected/`.
Captured `day1.json` and `line04.json` remain unchanged. Their real arithmetic
disagreements are negative cases, not totals to silently repair.

`test_ridership.py` checks coverage, raw series shape, dates, whole counts,
fare/QR/ONDC sums, cross-dataset reconciliation and the original normalizers.
`test_ridership_idempotence.py` checks whole-date replacement, valid reductions
and mixed revisions, history, replay, final-data precedence, capture ordering,
grouped write rollback, dry runs and legacy retries.

`test_upsert_control_flow.py` retains the shared strict-upsert matrix used by
parking/PHPDT. Their policies are unchanged.

Coverage is date-aware: line 04 joins the observed roster on 2026-10-13 (54 pairs
instead of 43). UTP is required from that baseline; TVM from 2026-10-26.
Core hourly slots are 00:00 and 04:00-23:00. Live snapshots require only elapsed
slots, plus any previously published nonzero overnight slot for that date.
Missing values are never invented zeros.

## publication and retries

Ridership fetches all three endpoints per attempt and validates before writing.
A failed check gets one fresh attempt after 60 seconds, unless the intended
date/cutoff no longer permits it. Tests mock sleeps and queue API responses.

- Valid snapshots replace that date across all three CSVs, including reductions.
- After retry, complete `/0` candidates with arithmetic findings may publish as
  `published_provisional`, with `validation_passed=false` and exit 2.
- Incomplete/malformed `/0` candidates stay `pending_retry`. Failed `/1`
  candidates stay `needs_review`; they never become finalized automatically.
- Valid `/1` publication is `published_final` and takes precedence over `/0`.
- Exit 0 includes validated revisions/no-ops; unrelated failures remain exit 1.
- `--dry-run` validates one attempt without sleeping or writing.
- `--strict` remains accepted, not a separate ridership policy.

Offline validation uses `recorded_at_utc`; a missing/naive capture timestamp
fails validation. Saved bundles retain URL-keyed responses, endpoint timings,
the day selector and findings, and can be replayed with `--api-response`.
Both attempts are retained after an initial failure. Accepted reductions also
retain their payload. Fresh observations advance the publication watermark;
replaying an identical capture creates no duplicate publication event.

`python -m collector.ridership --retry` remains available for held `/0` data and
legacy pending records. The background wait is 15 minutes; retries expire at
23:50 IST or when their IST day ends. Accepted final data resolves an obsolete
pending retry without querying a different date.

The daily workflow still collects parking independently on `/1`. Workflows
commit accepted changes and evidence together before reporting exit 2.
Exit 1 does not publish that collector's partial CSV writes. Tests make no live
requests and never use production archives as outputs.

Consistency checks cannot detect a coherent omission shared by every endpoint.
Statistical thresholds and reconstruction of November 7 are not included.
