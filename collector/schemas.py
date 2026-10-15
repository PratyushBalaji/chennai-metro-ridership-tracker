from dataclasses import dataclass, field


@dataclass(frozen=True)
class DatasetSchema:
    name: str
    key_columns: tuple[str, ...]
    sort_columns: tuple[str, ...] = field(default_factory=tuple)
    column_aliases: dict[str, str] = field(default_factory=dict)

    @property
    def effective_sort_columns(self) -> tuple[str, ...]:
        return self.sort_columns or self.key_columns


RIDERSHIP_DAILY = DatasetSchema(
    name="ridership_daily",
    key_columns=("Date",),
)

RIDERSHIP_HOURLY = DatasetSchema(
    name="ridership_hourly",
    key_columns=("Date", "Hour"),
)

RIDERSHIP_STATION = DatasetSchema(
    name="ridership_station",
    key_columns=("Date", "Line", "Station"),
)

PARKING_DAILY = DatasetSchema(
    name="parking_daily",
    key_columns=("Date",),
)

PARKING_HOURLY = DatasetSchema(
    name="parking_hourly",
    key_columns=("Date", "Hour"),
)

PARKING_STATION = DatasetSchema(
    name="parking_station",
    key_columns=("Date", "Line", "Station"),
    column_aliases={
        "Eight Wheleer": "Eight Wheeler",
    },
)

PHPDT_DAILY = DatasetSchema(
    name="phpdt_daily",
    key_columns=(
        "Date",
        "Line",
        "Direction",
        "Start Hour",
        "End Hour",
        "Start Station",
        "End Station",
    ),
)
