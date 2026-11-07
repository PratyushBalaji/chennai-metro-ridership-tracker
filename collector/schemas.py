from dataclasses import dataclass, field


RIDERSHIP_STATIONS = {
    "01": frozenset("SAL SAP SCC SCR SGE SGM SGU SHC SKP SLI SLM SMA SME SNW SOT SSA STC STE STG STL STR STT STV SWA SWD SWN".split()),
    "02": frozenset("SAE SAL SAN SAR SAT SCC SCM SEG SKM SKO SMM SNP SPC SSI SSN STI SVA".split()),
    "04": frozenset("IYP KAC KPM KUC MUL PBP POB POO PRJ SVA THL".split()),
}
LINE04_FROM = "2026-10-13"
TVM_FROM = "2026-10-26"
CORE_HOURS = frozenset(["00:00", *(f"{hour:02}:00" for hour in range(4, 24))])
RIDERSHIP_FARES = {
    "noOfSVC": "Store Value Card",
    "noOfNCMCcard": "Singara Chennai Card",
    "noOfPaperQR": "Paper QR",
    "noOfSVP": "SVP QR",
    "noOfMobileQR": "Mobile QR",
    "noOfEventQR": "Event QR",
    "noOfStaticQR": "Static QR",
    "noOfWhatsAppQR": "WhatsApp QR",
    "noOfPaytmQR": "Paytm QR",
    "noOfPhonePeQR": "PhonePe QR",
    "noOfCumtaQR": "Integrated QR (CUMTA)",
    "noOfPromotionalRideQR": "Promotional Ride QR",
    "noOfRedBusQR": "RedBus QR (ONDC)",
    "noOfRapidoQR": "Rapido QR (ONDC)",
    "noOfJusPayQR": "Namma Yatri QR (ONDC)",
    "noOfUberQR": "Uber QR (ONDC)",
    "noOfMilesKilometersQR": "Miles & Km QR (ONDC)",
    "noOfUTPQR": "UTP QR",
    "noOfTVM": "TVM",
}
CARD_FIELDS = frozenset(("noOfSVC", "noOfNCMCcard"))
ONDC_FIELDS = frozenset(key for key, name in RIDERSHIP_FARES.items() if name.endswith("(ONDC)"))
LEGACY_CARD_FIELDS = frozenset(("noOfCards", "noOfGroupCard", "noOfToken", "noOfTouristCard", "noOfTripcard"))


@dataclass(frozen=True)
class DatasetSchema:
    name: str
    key_columns: tuple[str, ...]
    column_aliases: dict[str, str] = field(default_factory=dict)


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
        "Start Station",
        "End Station",
    ),
)
