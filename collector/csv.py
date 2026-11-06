"""Small CSV helpers shared by the collectors."""

from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd


def read_csv(path):
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def write_csv_atomic(path, dataframe):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", delete=False,
                            dir=path.parent, suffix=".tmp") as file:
        temporary = Path(file.name)
        try:
            dataframe.to_csv(file, index=False)
        except BaseException:
            file.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
