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


def write_csv_group(outputs, after_write=None):
    """Restore the snapshot if replacement or its publication-log step fails."""
    originals, staged, replaced = {}, {}, []
    try:
        for raw_path, dataframe in outputs:
            path = Path(raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[path] = path.read_bytes() if path.exists() else None
            with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", delete=False,
                                    dir=path.parent, suffix=".tmp") as file:
                staged[path] = Path(file.name)
                dataframe.to_csv(file, index=False)
        for path, temporary in staged.items():
            temporary.replace(path)
            replaced.append(path)
        if after_write is not None:
            after_write()
    except BaseException:
        for path in reversed(replaced):
            content = originals[path]
            if content is None:
                path.unlink(missing_ok=True)
            else:
                with NamedTemporaryFile(mode="wb", delete=False, dir=path.parent, suffix=".tmp") as file:
                    backup = Path(file.name)
                    file.write(content)
                try:
                    backup.replace(path)
                finally:
                    backup.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
