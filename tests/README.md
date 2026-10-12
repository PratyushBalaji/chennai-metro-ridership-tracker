# collector benchmark

These tests cover the original scripts on main at `991a0a4`.

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

This does not require live counts to increase, reject regressions, check mixed
rows or implement arbitrary historical replay. Those are tests for the next
spec. The original scripts only skip when the API date equals the last CSV row.

To explicitly record a replacement response bundle:

```sh
python tests/record_api_responses.py --day 1 --output tests/fixtures/api/day1.json
```

Review changes to the JSON and expected CSVs together. Tests never regenerate
expected CSVs automatically.

GitHub Actions runs the benchmark on main, dev branches and PRs, and uploads
JUnit and coverage reports. The passing baseline is 19 tests; this is a
regression benchmark, not a guarantee about future CMRL availability.
