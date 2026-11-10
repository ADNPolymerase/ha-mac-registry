"""Diagnostics for MAC Registry: MACs, IPs and hostnames are masked."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import MacRegistryConfigEntry

REDACTED = "**REDACTED**"
# Keys whose values identify a network device
TO_REDACT = frozenset(
    {
        "mac",
        "ip",
        "hostname",
        "last_ips",
        "tracker",
        "matched_value",
        "router_device_id",
    }
)

# MAC (any separator, or 12 hex with a letter) and IPv4 found inside any string,
# e.g. a tracker entity id derived from a MAC
_MAC_IN_TEXT = re.compile(
    r"[0-9a-f]{2}([:\-_])(?:[0-9a-f]{2}\1){4}[0-9a-f]{2}"
    r"|(?<![0-9a-z])(?=[0-9]*[a-f])[0-9a-f]{12}(?![0-9a-z])",
    re.IGNORECASE,
)
_IPV4_IN_TEXT = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d])")


def _mask_text(value: str) -> str:
    return _IPV4_IN_TEXT.sub(REDACTED, _MAC_IN_TEXT.sub(REDACTED, value))


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (REDACTED if key in TO_REDACT and item else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _mask_text(value)
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MacRegistryConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with all network identifiers masked."""
    coordinator = entry.runtime_data
    table = coordinator.data
    rows = table.rows if table else []
    return {
        "options": dict(entry.options),
        "summary": coordinator.summary,
        "dhcp_source": coordinator.dhcp_available,
        "router_platforms": sorted(coordinator.router_platforms),
        "last_built": coordinator.last_built,
        "history_size": len(coordinator.store.data["macs"]),
        "override_count": len(coordinator.store.overrides),
        "overrides": [
            {"device_id": bool(value.get("device_id")), "label": bool(value.get("label"))}
            for value in coordinator.store.overrides.values()
        ],
        "rows": _redact(rows),
    }
