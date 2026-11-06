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


def append_csv(path, existing, incoming, *, zero_columns=()):
    existing = existing.copy()
    for column in zero_columns:
        if column in incoming and column not in existing:
            existing[column] = 0
    combined = pd.concat([existing.astype(object), incoming.astype(object)], ignore_index=True)
    write_csv_atomic(path, combined)


def normalize_parking_columns(dataframe):
    legacy, canonical = "Eight Wheleer", "Eight Wheeler"
    if legacy not in dataframe:
        return dataframe
    if canonical not in dataframe:
        return dataframe.rename(columns={legacy: canonical})
    dataframe = dataframe.copy()
    old, current = dataframe[legacy], dataframe[canonical]
    old_empty, current_empty = old.isna() | old.eq(""), current.isna() | current.eq("")
    equal = old.eq(current) | pd.to_numeric(old, errors="coerce").eq(pd.to_numeric(current, errors="coerce"))
    if (~old_empty & ~current_empty & ~equal).any():
        raise ValueError("Conflicting Eight Wheleer and Eight Wheeler values in parking data")
    dataframe[canonical] = current.mask(current_empty, old)
    return dataframe.drop(columns=[legacy])
