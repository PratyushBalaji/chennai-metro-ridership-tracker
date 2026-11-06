import os
from pathlib import Path
import subprocess
import sys

import pytest


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name,count", [("ridership", 3), ("parking", 3), ("phpdt", 1)])
def test_installed_module_defaults_to_data_root(name, count, tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTHON") and key != "DATASET_DATE"}
    result = subprocess.run(
        [sys.executable, "-E", "-m", f"collector.{name}", "--api-response",
         str(FIXTURES / "api/day1.json")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "data" / name).glob("*.csv"))) == count
    assert not (tmp_path / name).exists()
