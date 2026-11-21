"""Optional, recorded writes of a MAC to a device of the device registry.

MAC Registry is read-only unless the user turns this on. A MAC is only added
when the device does not declare it yet, every addition is recorded in
``.storage/mac_registry``, and only recorded additions are ever removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .store import MacRegistryStore

WRITTEN = "written"
ALREADY = "already"


@dataclass(slots=True, frozen=True)
class LostMac:
    """A written MAC that the device no longer declares, or whose device is gone."""

    mac: str
    device_id: str
    device_exists: bool


def _connection(mac: str) -> tuple[str, str]:
    return (dr.CONNECTION_NETWORK_MAC, mac)


def _refuse(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


@callback
def async_write_mac(
    hass: HomeAssistant,
    store: MacRegistryStore,
    mac: str,
    device_id: str,
    *,
    router_domains: frozenset[str] | set[str],
    bluetooth: bool = False,
) -> str:
    """Add the MAC to the device and record it.

    Returns ``written``, or ``already`` when the device declares the MAC on its
    own (nothing is recorded then). Raises ``ServiceValidationError`` when the
    write is refused: Bluetooth MAC, service device, a device of a router
    integration, or a MAC another device of the same integration declares.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        raise _refuse("unknown_device", device_id=device_id)
    name = device.name_by_user or device.name or device_id
    if bluetooth:
        raise _refuse("write_bluetooth", mac=mac)
    if device.entry_type is dr.DeviceEntryType.SERVICE:
        raise _refuse("write_service", device=name)
    entry = (
        hass.config_entries.async_get_entry(device.config_entry_id)
        if device.config_entry_id
        else None
    )
    if entry is None:
        raise _refuse("write_no_integration", device=name)
    if entry.domain in router_domains:
        raise _refuse("write_router", device=name)

    connection = _connection(mac)
    if connection in device.connections:
        return ALREADY
    if (
        other := dev_reg.async_get_device_by_connection(connection, entry.entry_id)
    ) is not None and other.id != device.id:
        raise _refuse(
            "write_collision", mac=mac, device=name, other=other.name_by_user or other.name or ""
        )
    previous = store.written.get(mac)
    if previous is not None and previous.get("device_id") != device.id:
        async_remove_written(hass, store, mac)
    try:
        dev_reg.async_update_device(device.id, new_connections=device.connections | {connection})
    except dr.DeviceConnectionCollisionError as err:
        raise _refuse("write_collision", mac=mac, device=name, other="") from err
    store.add_written(mac, device.id)
    return WRITTEN


@callback
def async_remove_written(hass: HomeAssistant, store: MacRegistryStore, mac: str) -> bool:
    """Remove a MAC that MAC Registry added, if it did. Returns True when removed."""
    record = store.remove_written(mac)
    if record is None:
        return False
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(record.get("device_id") or "")
    connection = _connection(mac)
    if device is None or connection not in device.connections:
        return False
    dev_reg.async_update_device(device.id, new_connections=device.connections - {connection})
    return True


@callback
def async_revert_all(hass: HomeAssistant, store: MacRegistryStore) -> int:
    """Remove every MAC MAC Registry added. Returns how many were removed."""
    return sum(async_remove_written(hass, store, mac) for mac in list(store.written))


@callback
def async_lost_written(hass: HomeAssistant, store: MacRegistryStore) -> list[LostMac]:
    """Written MACs the device no longer declares (its integration replaced them)."""
    dev_reg = dr.async_get(hass)
    lost: list[LostMac] = []
    for mac, record in store.written.items():
        device_id = str(record.get("device_id") or "")
        device = dev_reg.async_get(device_id)
        if device is None or _connection(mac) not in device.connections:
            lost.append(LostMac(mac, device_id, device is not None))
    return lost


def written_summary(store: MacRegistryStore, lost: list[LostMac]) -> dict[str, Any]:
    """Counts for the summary sensor and diagnostics."""
    return {"written": len(store.written), "written_lost": len(lost)}
