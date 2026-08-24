"""Constants for the Auckland Transport integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "auckland_transport"

# ---------------------------------------------------------------------------
# Config entry / options keys
# ---------------------------------------------------------------------------
CONF_API_KEY: Final = "api_key"
CONF_STOP_ID: Final = "stop_id"
CONF_STOP_TYPE: Final = "stop_type"
CONF_STOP_NAME: Final = "stop_name"
CONF_STOP_CODE: Final = "stop_code"

# Number of individual ``departure_N`` sensor entities to create.
CONF_DEPARTURE_SENSORS: Final = "departure_sensors"
# Emit the flat ``departure_N_*`` attributes the v0.2 card expects.
CONF_LEGACY_ATTRIBUTES: Final = "legacy_attributes"
# Hide services that only drop off (``pickup_type == 1``).
CONF_HIDE_ARRIVALS: Final = "hide_arrivals"
# Include school-bus routes (``route_type`` 712).
CONF_INCLUDE_SCHOOL_BUSES: Final = "include_school_buses"
# Only surface alerts whose active period covers now.
CONF_ONLY_ACTIVE_ALERTS: Final = "only_active_alerts"

DEFAULT_DEPARTURE_SENSORS: Final = 3
DEFAULT_LEGACY_ATTRIBUTES: Final = True
DEFAULT_HIDE_ARRIVALS: Final = False
DEFAULT_INCLUDE_SCHOOL_BUSES: Final = True
DEFAULT_ONLY_ACTIVE_ALERTS: Final = True

# Poll interval, fixed rather than configurable. Realtime data is one shared API
# call per interval regardless of how many stops are configured, so at 60 seconds
# the integration uses about 29% of a typical 35,000 request weekly allowance no
# matter how many stops there are. Auckland Transport refreshes its feed about
# every 30 seconds, so polling faster mostly re-downloads the same ~170 KB payload.
UPDATE_INTERVAL: Final = 60

# Size of the ``departures`` list on the board sensor, fixed at the maximum the
# attribute budget allows. ``fit_attributes`` still trims if a stop somehow
# produces a larger payload than measured.
BOARD_DEPARTURE_LIMIT: Final = 40

MAX_DEPARTURE_SENSORS: Final = 20

# Number of legacy ``departure_N_*`` attribute groups emitted for the old card.
LEGACY_ATTR_DEPARTURES: Final = 4
# Number of legacy ``alert_N_*`` attribute groups emitted for the old card.
LEGACY_ATTR_ALERTS: Final = 3

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
API_BASE_URL: Final = "https://api.at.govt.nz"
API_GTFS_BASE: Final = f"{API_BASE_URL}/gtfs/v3"
API_VERSIONS_ENDPOINT: Final = f"{API_GTFS_BASE}/versions"
API_STOPS_ENDPOINT: Final = f"{API_GTFS_BASE}/stops"
API_ROUTES_ENDPOINT: Final = f"{API_GTFS_BASE}/routes"
# One call returns trip updates, vehicle positions *and* service alerts.
API_REALTIME_COMBINED: Final = f"{API_BASE_URL}/realtime/legacy"

API_TIMEOUT: Final = 45
# ``filter[start_hour]`` is rejected when 0 and ``filter[hour_range]`` is capped at 30.
API_MIN_START_HOUR: Final = 1
API_MAX_HOUR_RANGE: Final = 30

# How often to re-check whether the published GTFS feed version changed.
STATIC_RECHECK_INTERVAL: Final = 6 * 60 * 60
# Before this local hour the previous service day's timetable is still live,
# because Auckland Transport expresses a trip running past midnight as an extended
# hour on the day it started: 00:45 on Saturday is "24:45" on Friday. Trips as late
# as hour 27 (03:00) appear on Friday and Saturday nights.
SCHEDULE_LOOKBACK_HOUR: Final = 5
# After a failed timetable request, wait this long before trying that day again
# rather than retrying on every update.
SCHEDULE_RETRY_COOLDOWN: Final = 600
# A cached realtime response is reused while it is younger than this fraction of
# the poll interval. Keeping it just under 1 means a coordinator firing on its own
# schedule always triggers exactly one shared fetch per interval, while other
# stops polling out of phase reuse that response instead of adding calls.
REALTIME_CACHE_MARGIN: Final = 0.95

# ---------------------------------------------------------------------------
# Transport modes
# ---------------------------------------------------------------------------
MODE_TRAIN: Final = "train"
MODE_BUS: Final = "bus"
MODE_FERRY: Final = "ferry"
MODE_TRAM: Final = "tram"
MODE_SCHOOL_BUS: Final = "school_bus"
MODE_UNKNOWN: Final = "unknown"

# GTFS ``route_type`` -> our mode name. Extended (7xx/9xx) values included.
ROUTE_TYPE_TO_MODE: Final[dict[int, str]] = {
    0: MODE_TRAM,
    1: MODE_TRAIN,
    2: MODE_TRAIN,
    3: MODE_BUS,
    4: MODE_FERRY,
    5: MODE_TRAM,
    6: MODE_TRAM,
    7: MODE_TRAIN,
    11: MODE_BUS,
    12: MODE_TRAIN,
    100: MODE_TRAIN,
    200: MODE_BUS,
    400: MODE_TRAIN,
    700: MODE_BUS,
    701: MODE_BUS,
    702: MODE_BUS,
    703: MODE_BUS,
    704: MODE_BUS,
    705: MODE_BUS,
    712: MODE_SCHOOL_BUS,
    713: MODE_SCHOOL_BUS,
    714: MODE_BUS,
    715: MODE_BUS,
    716: MODE_BUS,
    900: MODE_TRAM,
    1000: MODE_FERRY,
    1200: MODE_FERRY,
}

MODE_ICONS: Final[dict[str, str]] = {
    MODE_TRAIN: "mdi:train",
    MODE_BUS: "mdi:bus",
    MODE_FERRY: "mdi:ferry",
    MODE_TRAM: "mdi:tram",
    MODE_SCHOOL_BUS: "mdi:bus-school",
    MODE_UNKNOWN: "mdi:transit-connection-variant",
}

SCHOOL_BUS_ROUTE_TYPES: Final = frozenset({712, 713})

# Config-flow picker filters. ``all`` plus the three modes the old flow offered,
# so existing entries with a stored ``stop_type`` keep validating.
STOP_TYPE_ALL: Final = "all"
STOP_TYPES: Final = [STOP_TYPE_ALL, MODE_TRAIN, MODE_BUS, MODE_FERRY]

# ---------------------------------------------------------------------------
# Departure status values (also used by the card)
# ---------------------------------------------------------------------------
STATUS_SCHEDULED: Final = "scheduled"
STATUS_ON_TIME: Final = "on_time"
STATUS_LATE: Final = "late"
STATUS_EARLY: Final = "early"
STATUS_CANCELLED: Final = "cancelled"
STATUS_SKIPPED: Final = "skipped"
STATUS_ARRIVING: Final = "arriving"
STATUS_DEPARTING: Final = "departing"

# A delay inside this many seconds either way counts as on time.
ON_TIME_TOLERANCE: Final = 60

GTFS_SCHEDULE_CANCELED: Final = 3
GTFS_STOP_SKIPPED: Final = 1

OCCUPANCY_LABELS: Final[dict[int, str]] = {
    0: "empty",
    1: "many_seats_available",
    2: "few_seats_available",
    3: "standing_room_only",
    4: "crushed_standing_room_only",
    5: "full",
    6: "not_accepting_passengers",
}

# ---------------------------------------------------------------------------
# Attribute size budget
# ---------------------------------------------------------------------------
# Home Assistant's recorder drops attributes larger than 16384 bytes. Stay well
# under it so the extra attributes HA adds itself (friendly_name, icon, unit,
# device_class) can never push a state over the edge.
MAX_ATTR_BYTES: Final = 16384
ATTR_BYTE_BUDGET: Final = 13500
# Longest alert description we keep before truncating.
MAX_ALERT_TEXT: Final = 400

# ---------------------------------------------------------------------------
# Attribute names
# ---------------------------------------------------------------------------
ATTR_STOP_NAME: Final = "stop_name"
ATTR_STOP_CODE: Final = "stop_code"
ATTR_STOP_ID: Final = "stop_id"
ATTR_LOCATION_TYPE: Final = "location_type"
ATTR_STOP_LAT: Final = "stop_lat"
ATTR_STOP_LON: Final = "stop_lon"
ATTR_WHEELCHAIR_BOARDING: Final = "wheelchair_boarding"
ATTR_PLATFORM_CODE: Final = "platform_code"
ATTR_TRANSPORT_TYPE: Final = "transport_type"
ATTR_DEPARTURES: Final = "departures"
ATTR_ALERTS: Final = "alerts"
ATTR_SCHEMA_VERSION: Final = "schema_version"

# Bumped whenever the shape of ``departures`` / ``alerts`` changes so the card can
# adapt. The card treats "no schema_version" as the old flat-attribute layout.
SCHEMA_VERSION: Final = 2
