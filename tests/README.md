# collector benchmark

These tests execute the current root `ridership.py`, `parking.py` and `phpdt.py`.
Expected CSVs were captured from the original main scripts at `991a0a4`.

```sh
pip install -r requirements-test.txt
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
workflow input.

All three collectors accept the same fixed responses directly from the command line:

```sh
python ridership.py --api-response tests/fixtures/api/day1.json --output-dir test-output
python parking.py --api-response tests/fixtures/api/day1.json --output-dir test-output
python phpdt.py --api-response tests/fixtures/api/day1.json --output-dir test-output
```

This writes CSVs under `test-output/Ridership/`, `test-output/Parking/` and
`test-output/PHPDT/`. Without `--api-response`, the scripts call the live API.
Without `--output-dir`, they use the usual output folders. The CLI tests run
the root scripts with these flags, compare the CSVs and verify that no HTTP
calls happened. Parking's date fallback also reads from the JSON file;
`DATASET_DATE` still takes precedence when supplied by the workflow.

This does not require live counts to increase, reject regressions, check mixed
rows or implement arbitrary historical replay. Those are tests for the next
spec. The original scripts only skip when the API date equals the last CSV row.

To explicitly record a replacement response bundle:

```sh
python tests/record_api_responses.py --day 1 --output tests/fixtures/api/day1.json
```

Review changes to the JSON and expected CSVs together. Tests never regenerate
expected CSVs automatically.

GitHub Actions first runs each root collector with `--api-response` and
`--output-dir`, then runs pytest on main, dev branches and PRs. The generated
CSVs, JUnit results and coverage report are uploaded. The original functionality has 19 benchmark tests,
plus 13 tests for the file-input CLI. This is a
regression benchmark, not a guarantee about future CMRL availability.

## strict upsert tests

```sh
python -m pytest -m idempotence
```

`test_ridership_idempotence.py` runs the current root script through its CLI
using synthetic JSON in `fixtures/idempotence/cases.json` and prefilled CSVs.
The cases check equal rows, increases, decreases, mixed values, missing values,
blank backfills, historical inserts, live growth, final replays and next-day
inserts. They also check exit code 2, both anomaly statuses, UTC/IST timestamps,
append-only logs and row-level rejection while other keys update normally.

`test_upsert_control_flow.py` additionally checks all 27 lower/equal/higher
combinations across three numeric columns. Any decrease rejects the whole row;
otherwise increases update it and an identical row is a noop.

These tests exercise ridership's strict behavior. Parking and PHPDT retain
their original benchmark tests until their collection logic uses strict upsert.
CI automatically includes the new tests in its full pytest run.

## retries

`python ridership.py --retry` owns the retry flow. It checks the last log
record, waits 15 minutes when needed, collects live data when due, and appends
`resolved` or `expired`. Retries expire at 23:50 IST or when their IST day ends.
The returned date must match the pending record before any CSV writes.
Empty hourly timestamps leave CSVs alone and exit 2: live data is pending retry,
while yesterday's data needs review. Dry runs never write CSVs or logs.
Both workflows commit accepted CSV updates alongside
the anomaly log before rebasing, then report the collector's exit code.
