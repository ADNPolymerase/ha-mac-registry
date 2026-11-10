"""Summary sensor for MAC Registry."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, SENSOR_ATTR_MAX_BYTES, SENSOR_LIST_LIMIT, SUMMARY_ENTITY_ID
from .coordinator import MacRegistryConfigEntry, MacRegistryCoordinator
from .matcher import is_orphan, is_unseen_device

PARALLEL_UPDATES = 0

LIST_ATTRIBUTES = ("orphan_list", "unseen_device_list", "duplicate_list")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MacRegistryConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the summary sensor."""
    async_add_entities([MacRegistrySummarySensor(entry.runtime_data)])


class MacRegistrySummarySensor(CoordinatorEntity[MacRegistryCoordinator], SensorEntity):
    """Number of known MACs, with counters and short lists as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "summary"
    # Lists change often and can be large: keep them out of the recorder.
    _unrecorded_attributes = frozenset({*LIST_ATTRIBUTES, "by_confidence", "last_built"})

    def __init__(self, coordinator: MacRegistryCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_summary"
        # Fixed id whatever the UI language (a French name would give "resume")
        self.entity_id = SUMMARY_ENTITY_ID
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> int:
        """Return the number of known MACs."""
        return int(self.coordinator.summary.get("known", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return counters and capped lists (always below 16 kB)."""
        coordinator = self.coordinator
        summary = coordinator.summary
        rows = coordinator.data.rows if coordinator.data else []
        attrs: dict[str, Any] = {
            "known": summary.get("known", 0),
            "matched": summary.get("matched", 0),
            "router_linked": summary.get("router_linked", 0),
            "labelled": summary.get("labelled", 0),
            "orphans": summary.get("orphans", 0),
            "unseen_devices": summary.get("unseen_devices", 0),
            "random": summary.get("random", 0),
            "online": summary.get("online", 0),
            "duplicates": summary.get("duplicates", 0),
            "by_confidence": summary.get("by_confidence", {}),
            "dhcp_source": "active" if coordinator.dhcp_available else "inactive",
            "last_built": coordinator.last_built,
            "orphan_list": [
                {"mac": r["mac"], "ip": r["ip"], "hostname": r["hostname"], "tracker": r["tracker"]}
                for r in rows
                if is_orphan(r)
            ],
            "unseen_device_list": [
                {"mac": r["mac"], "device": r["device_name"], "integration": r["integration"]}
                for r in rows
                if is_unseen_device(r)
            ],
            "duplicate_list": [
                {"mac": d["mac"], "devices": [dev["device_name"] for dev in d["devices"]]}
                for d in (coordinator.data.duplicates if coordinator.data else [])
            ],
        }
        return cap_attributes(attrs)


def cap_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Trim list attributes so the JSON stays below the size limit."""
    limit = SENSOR_LIST_LIMIT
    truncated = False
    for key in LIST_ATTRIBUTES:
        if len(attrs[key]) > limit:
            attrs[key] = attrs[key][:limit]
            truncated = True
    while len(json.dumps(attrs, default=str)) > SENSOR_ATTR_MAX_BYTES and limit > 0:
        limit //= 2
        for key in LIST_ATTRIBUTES:
            attrs[key] = attrs[key][:limit]
        truncated = True
    attrs["lists_truncated"] = truncated
    return attrs
