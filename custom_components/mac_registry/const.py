"""Constants for MAC Registry."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "mac_registry"
NAME: Final = "MAC Registry"

# Storage
STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 30  # seconds

# Options
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ROUTER_INTEGRATIONS: Final = "router_integrations"
CONF_IGNORE_RANDOM: Final = "ignore_random"
CONF_USE_DHCP: Final = "use_dhcp"

DEFAULT_SCAN_INTERVAL: Final = 15  # minutes
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 1440
DEFAULT_IGNORE_RANDOM: Final = False
DEFAULT_USE_DHCP: Final = True

# Event-driven refresh debounce (seconds)
DEBOUNCE_COOLDOWN: Final = 10.0

# Device tracker contract (strings kept local so the integration does not
# import the device_tracker component, see hassfest dependency rules)
TRACKER_DOMAIN: Final = "device_tracker"
ATTR_SOURCE_TYPE: Final = "source_type"
SOURCE_TYPE_ROUTER: Final = "router"
ATTR_TRACKER_IP: Final = "ip"
ATTR_TRACKER_MAC: Final = "mac"
ATTR_TRACKER_HOST_NAME: Final = "host_name"
ATTR_TRACKER_CONNECTION: Final = "connection"
STATE_HOME: Final = "home"

# Config entry keys read for IP/host matching. Nothing else is ever read.
CONFIG_ENTRY_HOST_KEYS: Final = ("host", "ip_address")

# Confidence levels, best first
CONF_MANUAL: Final = "manual"
CONF_TRACKER_REGISTRY: Final = "tracker+registry"
CONF_REGISTRY: Final = "registry"
CONF_DISCOVERY: Final = "discovery"
CONF_HOSTNAME: Final = "hostname"
CONF_IP: Final = "ip"
CONF_UNMATCHED: Final = "unmatched"
CONFIDENCE_ORDER: Final = (
    CONF_MANUAL,
    CONF_TRACKER_REGISTRY,
    CONF_REGISTRY,
    CONF_DISCOVERY,
    CONF_HOSTNAME,
    CONF_IP,
    CONF_UNMATCHED,
)

# Observation sources
SRC_REGISTRY: Final = "registry"
SRC_ROUTER: Final = "router"
SRC_DHCP: Final = "dhcp"
SRC_HISTORY: Final = "history"
SRC_MANUAL: Final = "manual"

# History
MAX_IPS: Final = 5
MAX_DAYS: Final = 14
HISTORY_RETENTION_DAYS: Final = 365
ORPHAN_MIN_DAYS: Final = 3

# Events
EVENT_NEW_MAC: Final = "mac_registry_new_mac"
EVENT_IP_CHANGED: Final = "mac_registry_ip_changed"

# Services
SERVICE_LOOKUP: Final = "lookup"
SERVICE_EXPORT: Final = "export"
SERVICE_REFRESH: Final = "refresh"
SERVICE_LINK: Final = "link"
SERVICE_UNLINK: Final = "unlink"
ATTR_MAC: Final = "mac"
ATTR_DEVICE_ID: Final = "device_id"
ATTR_LABEL: Final = "label"
ATTR_QUERY: Final = "query"
ATTR_PATH: Final = "path"
ATTR_FORMAT: Final = "format"
EXPORT_FORMATS: Final = ("csv", "markdown")

# Repairs
ISSUE_ORPHANS: Final = "orphan_macs"
ISSUE_DUPLICATES: Final = "duplicate_macs"
ISSUE_LIST_LIMIT: Final = 10

# Sensor
SUMMARY_ENTITY_ID: Final = "sensor.mac_registry_summary"
SENSOR_LIST_LIMIT: Final = 40
SENSOR_ATTR_MAX_BYTES: Final = 15000
