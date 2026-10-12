import json
from pathlib import Path
import runpy
import sys

import pytest
import responses


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def api_bundle():
    return json.loads((FIXTURES / "api/day1.json").read_text(encoding="utf-8"))


@pytest.fixture
def http_mock(api_bundle):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        for url, payload in api_bundle["responses"].items():
            mock.get(url, json=payload)
        yield mock


@pytest.fixture
def run_collector(monkeypatch, tmp_path):
    for folder in ("Ridership", "Parking", "PHPDT"):
        (tmp_path / folder).mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATASET_DATE", raising=False)

    def run(name, *args):
        monkeypatch.setattr(sys, "argv", [f"{name}.py", *map(str, args)])
        runpy.run_path(str(ROOT / f"{name}.py"), run_name="__main__")
    return run
