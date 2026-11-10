"""Simulated Home Assistant inventory, documentation ranges only.

IPs come from 192.0.2.0/24 (RFC 5737) and MACs from 00:00:5e:00:53:xx
(RFC 7042). No real device, address or name is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mac_registry.config_flow import DEFAULT_OPTIONS
from custom_components.mac_registry.const import DOMAIN

SECRET = "not-a-real-password"

LOG_SHELLY = "RPC call error ... shelly1g3-00005e005301"
LOG_PLUG = (
    "Cannot connect to host 192.0.2.207:80 ssl:default [Connect call failed ('192.0.2.207', 80)]"
)
LOG_VACUUM = "Roomba 192.0.2.198 connection lost ([Errno 113] Host is unreachable)"


@dataclass
class Scenario:
    """Ids created by :func:`async_setup_scenario`."""

    entries: dict[str, MockConfigEntry] = field(default_factory=dict)
    devices: dict[str, str] = field(default_factory=dict)


def router_attrs(mac: str, ip: str | None, host: str | None, **extra: Any) -> dict[str, Any]:
    """Attributes as tplink_router exposes them (upper case, dashes)."""
    attrs: dict[str, Any] = {
        "source_type": "router",
        "mac": mac.upper().replace(":", "-"),
        "connection": "wired",
        "ap_name": "*Example-AP",
        "online_time": 40.64,
    }
    if ip is not None:
        attrs["ip"] = ip
    if host is not None:
        attrs["host_name"] = host
    attrs.update(extra)
    return attrs


def add_entry(hass: HomeAssistant, sc: Scenario, domain: str, **data: Any) -> MockConfigEntry:
    """Add a config entry (not set up) for another integration."""
    entry = MockConfigEntry(domain=domain, data=data, unique_id=data.pop("unique_id", None))
    entry.add_to_hass(hass)
    sc.entries[domain] = entry
    return entry


def add_device(
    hass: HomeAssistant,
    sc: Scenario,
    key: str,
    entry: MockConfigEntry,
    name: str,
    *,
    connections: set[tuple[str, str]] | None = None,
    entities: tuple[str, ...] = (),
    manufacturer: str = "Example",
    model: str = "Model",
) -> str:
    """Add a device and its entities."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, key)},
        connections=connections or set(),
        name=name,
        manufacturer=manufacturer,
        model=model,
    )
    for entity_id in entities:
        domain, object_id = entity_id.split(".", 1)
        ent_reg.async_get_or_create(
            domain,
            entry.domain,
            f"{key}_{object_id}",
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device.id,
        )
    sc.devices[key] = device.id
    return device.id


def add_tracker(
    hass: HomeAssistant,
    sc: Scenario,
    object_id: str,
    mac: str,
    ip: str | None,
    host: str | None,
    state: str = "home",
) -> str:
    """Add a tplink_router tracker with its router-created device."""
    router = sc.entries["tplink_router"]
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=router.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        name=host or mac,
    )
    entity = ent_reg.async_get_or_create(
        "device_tracker",
        "tplink_router",
        mac,
        suggested_object_id=object_id,
        config_entry=router,
        device_id=device.id,
    )
    hass.states.async_set(entity.entity_id, state, router_attrs(mac, ip, host))
    return entity.entity_id


async def async_setup_scenario(hass: HomeAssistant) -> Scenario:
    """Create a small but representative inventory."""
    sc = Scenario()
    add_entry(hass, sc, "tplink_router", host="192.0.2.1", password=SECRET)

    # Shelly found by MAC (registry) and seen by the router -> tracker+registry
    shelly = add_entry(hass, sc, "shelly", host="192.0.2.31", password=SECRET)
    add_device(
        hass,
        sc,
        "shelly",
        shelly,
        "Example Shelly",
        connections={(dr.CONNECTION_NETWORK_MAC, "00:00:5E:00:53:01")},
        entities=("switch.example_shelly", "sensor.example_shelly_power"),
        manufacturer="Shelly",
        model="1 Gen3",
    )
    add_tracker(hass, sc, "shelly1g3", "00:00:5e:00:53:01", "192.0.2.31", "shelly1g3-00005e005301")

    # Plug resolved from an IP in a log line
    tuya = add_entry(hass, sc, "tuya")
    add_device(
        hass,
        sc,
        "plug",
        tuya,
        "Example Plug",
        connections={(dr.CONNECTION_NETWORK_MAC, "00:00:5e:00:53:20")},
        entities=("switch.example_plug",),
    )
    add_tracker(hass, sc, "example_plug", "00:00:5e:00:53:20", "192.0.2.207", "Example-Plug")

    # Camera, same pattern as the observed tracker format
    onvif = add_entry(hass, sc, "onvif", host="192.0.2.14", password=SECRET)
    add_device(
        hass,
        sc,
        "camera",
        onvif,
        "Example Camera",
        connections={(dr.CONNECTION_NETWORK_MAC, "00-00-5E-00-53-14")},
        entities=("camera.example_camera",),
    )
    add_tracker(hass, sc, "example_camera", "00:00:5e:00:53:14", "192.0.2.14", "Example-Camera")

    # Vacuum: no MAC in the registry, only an IP in its config entry -> ip
    roomba = add_entry(hass, sc, "roomba", host="192.0.2.198", blid="x", password=SECRET)
    add_device(hass, sc, "vacuum", roomba, "Example Vacuum", entities=("vacuum.example_vacuum",))
    add_tracker(hass, sc, "00_00_5e_00_53_8a", "00:00:5e:00:53:8a", "192.0.2.198", None)

    # ESP: registry only knows its Bluetooth MAC, the router sees ESP_005351
    esphome = add_entry(hass, sc, "esphome", host="example-sensor.local")
    add_device(
        hass,
        sc,
        "esp",
        esphome,
        "Example Sensor",
        connections={(dr.CONNECTION_BLUETOOTH, "00:00:5e:00:53:51")},
        entities=("sensor.example_temperature",),
    )
    add_tracker(hass, sc, "esp_005351", "00:00:5e:00:53:50", "192.0.2.50", "ESP_005351")

    # Fan: config entry IP only, never seen by the router
    dyson = add_entry(hass, sc, "dyson_local", host="192.0.2.99", password=SECRET)
    add_device(hass, sc, "fan", dyson, "Example Fan", entities=("fan.example_fan",))

    # Hub: MAC known to HA, never seen by the router -> unseen device
    hue = add_entry(hass, sc, "hue", host="192.0.2.60")
    add_device(
        hass,
        sc,
        "hub",
        hue,
        "Example Hub",
        connections={(dr.CONNECTION_NETWORK_MAC, "00:00:5e:00:53:60")},
        entities=("light.example_light",),
    )

    # Same appliance through two integrations -> duplicate
    localtuya = add_entry(hass, sc, "localtuya")
    add_device(
        hass,
        sc,
        "dup_cloud",
        tuya,
        "Example Heater (cloud)",
        connections={(dr.CONNECTION_NETWORK_MAC, "00:00:5e:00:53:70")},
    )
    add_device(
        hass,
        sc,
        "dup_local",
        localtuya,
        "Example Heater (local)",
        connections={(dr.CONNECTION_NETWORK_MAC, "00005e005370")},
    )

    # Zigbee device: IEEE address, not a MAC
    z2m = add_entry(hass, sc, "mqtt")
    add_device(
        hass,
        sc,
        "zigbee",
        z2m,
        "Example Zigbee",
        connections={("zigbee", "0x00005eef10000001")},
        entities=("binary_sensor.example_door",),
    )

    # Orphans: a laptop (regular MAC) and a phone (random MAC)
    add_tracker(hass, sc, "example_laptop", "00:00:5e:00:53:41", "192.0.2.41", "Example-Laptop")
    add_tracker(hass, sc, "example_phone", "02:00:5e:00:53:40", "192.0.2.40", "Example-Phone")

    # A non-router tracker is ignored
    hass.states.async_set(
        "device_tracker.example_gps", "home", {"source_type": "gps", "latitude": 0}
    )
    await hass.async_block_till_done()
    return sc


async def async_setup_integration(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set up MAC Registry."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="MAC Registry", data={}, options={**DEFAULT_OPTIONS, **(options or {})}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
