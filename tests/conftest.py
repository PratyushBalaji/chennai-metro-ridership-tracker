import json
from pathlib import Path
import importlib
import sys
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import responses


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def api_bundle():
    return json.loads((FIXTURES / "api/day1.json").read_text(encoding="utf-8"))


@pytest.fixture
def snapshot_bundle():
    return json.loads((FIXTURES / "api/consistent_ridership.json").read_text(encoding="utf-8"))


@pytest.fixture
def snapshot_clock(monkeypatch):
    from collector import ridership, anomalies

    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 11, 7, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(ridership, "datetime", clock)
    monkeypatch.setattr(anomalies, "datetime", clock)
    monkeypatch.setattr(ridership, "sleep", Mock())
    return clock


@pytest.fixture
def collect_snapshot(tmp_path, snapshot_clock):
    from collector import ridership

    def collect(bundle, day="1", **kwargs):
        path = tmp_path / "input.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        return ridership.collect_ridership(day=day, api_response=path,
                                          output_dir=tmp_path / "output", **kwargs)
    return collect


@pytest.fixture
def http_mock(api_bundle, snapshot_bundle, snapshot_clock, request):
    if getattr(getattr(request.node, "callspec", None), "params", {}).get("script") == "ridership":
        api_bundle = snapshot_bundle
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        for url, payload in api_bundle["responses"].items():
            mock.get(url, json=payload)
        yield mock


@pytest.fixture
def run_collector(monkeypatch, tmp_path):
    for folder in ("ridership", "parking", "phpdt"):
        (tmp_path / folder).mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATASET_DATE", raising=False)

    def run(name, *args):
        if "--output-dir" not in args:
            args = (*args, "--output-dir", tmp_path)
        monkeypatch.setattr(sys, "argv", [f"collector.{name}", *map(str, args)])
        importlib.import_module(f"collector.{name}").main()
    return run


def pytest_configure(config):
    (config.rootpath / ".cache").mkdir(exist_ok=True)
