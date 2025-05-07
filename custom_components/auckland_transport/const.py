"""Constants for the Auckland Transport integration."""
from typing import Final

DOMAIN: Final = "auckland_transport"
CONF_API_KEY: Final = "api_key"
CONF_STOP_ID: Final = "stop_id"
CONF_STOP_TYPE: Final = "stop_type"

# Stop types
STOP_TYPE_ALL: Final = "all"
STOP_TYPE_TRAIN: Final = "train"
STOP_TYPE_BUS: Final = "bus"
STOP_TYPE_FERRY: Final = "ferry"
STOP_TYPES: Final = [STOP_TYPE_ALL, STOP_TYPE_TRAIN, STOP_TYPE_BUS, STOP_TYPE_FERRY]

# API endpoints
API_BASE_URL: Final = "https://api.at.govt.nz/gtfs/v3"
API_STOPS_ENDPOINT: Final = f"{API_BASE_URL}/stops"

# Default update interval in seconds (60 seconds)
DEFAULT_SCAN_INTERVAL: Final = 60

# Data update coordinator update interval for general data (5 minutes)
UPDATE_INTERVAL: Final = 300

# Service attributes
ATTR_STOP_NAME: Final = "stop_name"
ATTR_STOP_CODE: Final = "stop_code"
ATTR_LOCATION_TYPE: Final = "location_type"
ATTR_STOP_LAT: Final = "stop_lat"
ATTR_STOP_LON: Final = "stop_lon"
ATTR_WHEELCHAIR_BOARDING: Final = "wheelchair_boarding"
