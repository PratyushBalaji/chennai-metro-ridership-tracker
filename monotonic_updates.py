import json
import os
from typing import Dict, List, Tuple

import pandas as pd


DEFAULT_DROP_THRESHOLD_PCT = 20.0


class MonotonicRegressionError(RuntimeError):
    pass


def read_csv_or_empty(filename: str) -> pd.DataFrame:
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    if not os.path.exists(filename):
        return pd.DataFrame()

    try:
        return pd.read_csv(filename)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _serialize_key_dict(key_cols: List[str], key: Tuple) -> Dict[str, str]:
    return {key_cols[i]: str(key[i]) for i in range(len(key_cols))}


def _to_float(value):
    if pd.isna(value):
        return None

    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    return float(numeric)


def _columns_union(existing_df: pd.DataFrame, new_df: pd.DataFrame) -> List[str]:
    ordered_cols = list(existing_df.columns)
    for col in new_df.columns:
        if col not in ordered_cols:
            ordered_cols.append(col)
    return ordered_cols


def upsert_monotonic(
    existing_df: pd.DataFrame,
    new_df: pd.DataFrame,
    key_cols: List[str],
    dataset_name: str,
    drop_threshold_pct: float = DEFAULT_DROP_THRESHOLD_PCT,
):
    if new_df.empty:
        return existing_df.copy(), {"appended": 0, "updated": 0, "skipped_decreasing": 0}

    if existing_df.empty:
        updated_df = new_df.copy().reset_index(drop=True)
        return updated_df, {"appended": len(updated_df), "updated": 0, "skipped_decreasing": 0}

    cols = _columns_union(existing_df, new_df)
    existing = existing_df.reindex(columns=cols).copy()
    incoming = new_df.reindex(columns=cols).copy()

    value_cols = [col for col in cols if col not in key_cols]

    key_to_idx = {
        tuple(existing.loc[idx, key_cols].tolist()): idx
        for idx in existing.index
    }

    appended = 0
    updated = 0
    skipped_decreasing = 0
    severe_regressions = []

    for _, new_row in incoming.iterrows():
        key = tuple(new_row[col] for col in key_cols)

        if key not in key_to_idx:
            existing = pd.concat([existing, pd.DataFrame([new_row])], ignore_index=True)
            key_to_idx[key] = existing.index[-1]
            appended += 1
            continue

        old_idx = key_to_idx[key]
        old_row = existing.loc[old_idx]

        monotonic_for_row = True

        for col in value_cols:
            old_value = _to_float(old_row[col])
            new_value = _to_float(new_row[col])

            if old_value is None or new_value is None:
                continue

            if new_value < old_value:
                monotonic_for_row = False

                if old_value > 0:
                    drop_pct = ((old_value - new_value) / old_value) * 100
                    if drop_pct > drop_threshold_pct:
                        severe_regressions.append(
                            {
                                "dataset": dataset_name,
                                "key": _serialize_key_dict(key_cols, key),
                                "column": col,
                                "old_value": old_value,
                                "new_value": new_value,
                                "drop_pct": round(drop_pct, 2),
                            }
                        )

        if monotonic_for_row:
            for col in value_cols:
                if pd.notna(new_row[col]):
                    existing.at[old_idx, col] = new_row[col]
            updated += 1
        else:
            skipped_decreasing += 1

    if severe_regressions:
        error_payload = {
            "threshold_pct": drop_threshold_pct,
            "severe_regressions": severe_regressions[:200],
            "incoming_sample": incoming.head(50).to_dict(orient="records"),
        }
        raise MonotonicRegressionError(
            f"[{dataset_name}] Severe regression detected.\n{json.dumps(error_payload, indent=2, default=str)}"
        )

    return existing.reset_index(drop=True), {
        "appended": appended,
        "updated": updated,
        "skipped_decreasing": skipped_decreasing,
    }
