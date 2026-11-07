from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from collector.schemas import DatasetSchema
from collector.csv import read_csv, write_csv_atomic


@dataclass(frozen=True)
class CellConflict:
    column: str
    existing: Any
    incoming: Any
    reason: str


@dataclass
class RowDecision:
    dataset: str
    key: dict[str, Any]
    action: str
    updated_columns: list[str] = field(default_factory=list)
    conflicts: list[CellConflict] = field(default_factory=list)


@dataclass
class UpsertResult:
    dataframe: pd.DataFrame
    decisions: list[RowDecision]

    @property
    def inserted(self) -> int:
        return sum(decision.action == "inserted" for decision in self.decisions)

    @property
    def updated(self) -> int:
        return sum(decision.action == "updated" for decision in self.decisions)

    @property
    def anomalies(self) -> int:
        return sum(decision.action == "anomaly" for decision in self.decisions)

    @property
    def conflicts(self) -> int:
        return sum(len(decision.conflicts) for decision in self.decisions)


def upsert_csv(path: str | Path, incoming: pd.DataFrame, schema: DatasetSchema, *, allow_increases: bool = True) -> UpsertResult:
    existing = read_csv(path)
    result = upsert_rows(existing, incoming, schema, allow_increases=allow_increases)
    write_csv_atomic(path, result.dataframe)
    return result


def upsert_rows(existing: pd.DataFrame, incoming: pd.DataFrame, schema: DatasetSchema, *, allow_increases: bool = True, zero_columns: tuple[str, ...] = ()) -> UpsertResult:
    existing = normalize_columns(existing.copy(), schema)
    incoming = normalize_columns(incoming.copy(), schema)
    for column in zero_columns:
        if column in incoming and column not in existing:
            existing[column] = 0

    _require_columns(incoming, schema.key_columns, "incoming")
    if not existing.empty:
        _require_columns(existing, schema.key_columns, "existing")

    _require_unique_keys(incoming, schema)
    if not existing.empty:
        _require_unique_keys(existing, schema)

    all_columns = _ordered_union(existing.columns, incoming.columns)
    existing = existing.reindex(columns=all_columns).astype(object)
    incoming = incoming.reindex(columns=all_columns).astype(object)

    if existing.empty:
        result_df = sort_rows(incoming, schema)
        return UpsertResult(
            dataframe=result_df,
            decisions=[
                RowDecision(schema.name, _key_dict(row, schema), "inserted")
                for _, row in incoming.iterrows()
            ],
        )

    result_df = existing.copy()
    index_by_key = dict(zip(
        result_df[list(schema.key_columns)].itertuples(index=False, name=None),
        result_df.index,
    ))

    decisions: list[RowDecision] = []

    for _, incoming_row in incoming.iterrows():
        key_tuple = _key_tuple(incoming_row, schema)
        key = _key_dict(incoming_row, schema)

        if key_tuple not in index_by_key:
            row_index = len(result_df)
            while row_index in result_df.index:
                row_index += 1
            result_df.loc[row_index] = incoming_row
            index_by_key[key_tuple] = row_index
            decisions.append(RowDecision(schema.name, key, "inserted"))
            continue

        row_index = index_by_key[key_tuple]
        existing_row = result_df.loc[row_index]
        updates: dict[str, Any] = {}
        conflicts: list[CellConflict] = []

        for column in all_columns:
            if column in schema.key_columns:
                continue

            existing_value = existing_row[column]
            incoming_value = incoming_row[column]

            if _both_empty(existing_value, incoming_value) or _values_equal(existing_value, incoming_value):
                continue

            if _is_empty(existing_value) and not _is_empty(incoming_value):
                updates[column] = incoming_value
                continue

            if not _is_empty(existing_value) and _is_empty(incoming_value):
                conflicts.append(CellConflict(column, existing_value, incoming_value, "incoming_value_missing"))
                continue

            existing_number = _to_number(existing_value)
            incoming_number = _to_number(incoming_value)

            if existing_number is not None and incoming_number is not None:
                if incoming_number > existing_number:
                    if allow_increases:
                        updates[column] = incoming_value
                    else:
                        conflicts.append(
                            CellConflict(column, existing_value, incoming_value, "incoming_value_changed")
                        )
                elif incoming_number < existing_number:
                    conflicts.append(
                        CellConflict(column, existing_value, incoming_value, "incoming_value_decreased")
                    )
                continue

            conflicts.append(CellConflict(column, existing_value, incoming_value, "incoming_value_changed"))

        if conflicts:
            decisions.append(RowDecision(schema.name, key, "anomaly", conflicts=conflicts))
            continue

        for column, value in updates.items():
            result_df.at[row_index, column] = value

        if updates:
            action = "updated"
        else:
            action = "noop"

        decisions.append(RowDecision(schema.name, key, action, list(updates), conflicts))

    return UpsertResult(sort_rows(result_df, schema), decisions)


def normalize_columns(dataframe: pd.DataFrame, schema: DatasetSchema) -> pd.DataFrame:
    for alias, canonical in schema.column_aliases.items():
        if alias not in dataframe.columns:
            continue

        if canonical not in dataframe.columns:
            dataframe = dataframe.rename(columns={alias: canonical})
            continue

        for row_label, legacy, current in zip(dataframe.index, dataframe[alias], dataframe[canonical]):
            if not _is_empty(legacy) and not _is_empty(current) and not _values_equal(legacy, current):
                raise ValueError(
                    f"{schema.name}: conflicting values for {alias!r} and {canonical!r} "
                    f"at row {row_label}: {legacy!r} vs {current!r}"
                )
        dataframe[canonical] = dataframe[canonical].where(
            ~dataframe[canonical].map(_is_empty),
            dataframe[alias],
        )
        dataframe = dataframe.drop(columns=[alias])

    return dataframe


def sort_rows(dataframe: pd.DataFrame, schema: DatasetSchema) -> pd.DataFrame:
    sort_columns = [column for column in schema.key_columns if column in dataframe.columns]
    if not sort_columns:
        return dataframe.reset_index(drop=True)
    return dataframe.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)


def _require_columns(dataframe: pd.DataFrame, columns: tuple[str, ...], label: str) -> None:
    missing = [column for column in columns if column not in dataframe.columns]
    if missing:
        raise ValueError(f"{label} data is missing required key columns: {', '.join(missing)}")


def _require_unique_keys(dataframe: pd.DataFrame, schema: DatasetSchema) -> None:
    duplicates = dataframe.duplicated(list(schema.key_columns), keep=False)
    if duplicates.any():
        duplicate_keys = dataframe.loc[duplicates, list(schema.key_columns)].drop_duplicates()
        raise ValueError(
            f"{schema.name} contains duplicate natural keys: "
            f"{duplicate_keys.to_dict(orient='records')}"
        )


def _ordered_union(left: pd.Index, right: pd.Index) -> list[str]:
    columns = list(left)
    columns.extend(column for column in right if column not in columns)
    return columns


def _key_tuple(row: pd.Series, schema: DatasetSchema) -> tuple[Any, ...]:
    return tuple(row[column] for column in schema.key_columns)


def _key_dict(row: pd.Series, schema: DatasetSchema) -> dict[str, Any]:
    return {column: row[column] for column in schema.key_columns}


def _to_number(value: Any) -> float | None:
    if _is_empty(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except TypeError:
        pass
    return value == ""


def _both_empty(left: Any, right: Any) -> bool:
    return _is_empty(left) and _is_empty(right)


def _values_equal(left: Any, right: Any) -> bool:
    left_number = _to_number(left)
    right_number = _to_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(left) == str(right)
