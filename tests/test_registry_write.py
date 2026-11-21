"""Optional, recorded writes of a MAC to a device of the device registry."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
import pytest

from custom_components.mac_registry.const import (
    CONF_WRITE_CONNECTIONS,
    DOMAIN,
    ISSUE_ORPHANS,
    STORAGE_KEY,
    SUMMARY_ENTITY_ID,
)
from custom_components.mac_registry.registry_write import async_write_mac

from .common import (
    add_device,
    add_entry,
    add_router_device,
    add_tracker,
    async_setup_integration,
    async_setup_scenario,
)

LAMP_MAC = "00:00:5e:00:53:d1"
TAKEN_MAC = "00:00:5e:00:53:d2"
SPEAKER_MAC = "00:00:5e:00:53:d3"
CLIENT_MAC = "00:00:5e:00:53:d4"


def _mac(mac: str) -> tuple[str, str]:
    return (dr.CONNECTION_NETWORK_MAC, mac)


def _connections(hass: HomeAssistant, device_id: str) -> set[tuple[str, str]]:
    device = dr.async_get(hass).async_get(device_id)
    assert device is not None
    return set(device.connections)


async def _setup(
    hass: HomeAssistant, hass_storage: dict[str, Any], write: bool = True
) -> tuple[Any, Any]:
    """Scenario plus devices that declare no MAC, seen by the router."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "initialized": True,
            "macs": {
                mac: {
                    "first_seen": "2026-11-01T08:00:00+00:00",
                    "last_seen": "2026-11-03T08:00:00+00:00",
                    "hostname": None,
                    "ips": [],
                    "days": ["2026-11-01", "2026-11-02", "2026-11-03"],
                }
                for mac in (LAMP_MAC, SPEAKER_MAC)
            },
            "overrides": {},
        },
    }
    sc = await async_setup_scenario(hass)
    lights = add_entry(hass, sc, "example_lights")
    add_device(hass, sc, "lamp", lights, "Example Lamp Desk", entities=("light.desk",))
    add_device(
        hass,
        sc,
        "other_lamp",
        lights,
        "Example Lamp Hall",
        connections={_mac(TAKEN_MAC)},
        entities=("light.hall",),
    )
    speakers = add_entry(hass, sc, "example_audio")
    add_device(
        hass, sc, "speaker", speakers, "Example Speaker Kitchen", entities=("media_player.kitchen",)
    )
    add_device(
        hass,
        sc,
        "service",
        speakers,
        "Example Audio Hub",
        entities=("update.example_audio",),
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    add_tracker(hass, sc, "lamp_desk", LAMP_MAC, "192.0.2.211", "Example-Lamp-Desk")
    add_tracker(hass, sc, "speaker_kitchen", SPEAKER_MAC, "192.0.2.213", "Speaker-Kitchen")
    sc.devices["router_client"] = add_router_device(hass, sc, CLIENT_MAC, "Example Client")
    await hass.async_block_till_done()
    entry = await async_setup_integration(hass, {CONF_WRITE_CONNECTIONS: write})
    return sc, entry


async def _link(hass: HomeAssistant, **data: Any) -> None:
    await hass.services.async_call(DOMAIN, "link", data, blocking=True)


async def test_link_writes_the_mac_and_unlink_removes_it(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The MAC is added once, recorded, and removed with the link."""
    sc, entry = await _setup(hass, hass_storage)
    lamp = sc.devices["lamp"]
    before = _connections(hass, lamp)

    await _link(hass, mac=LAMP_MAC, device_id=lamp)
    assert _connections(hass, lamp) == before | {_mac(LAMP_MAC)}
    store = entry.runtime_data.store
    assert store.written[LAMP_MAC]["device_id"] == lamp
    assert entry.runtime_data.data.by_mac[LAMP_MAC]["device_id"] == lamp
    attrs = hass.states.get(SUMMARY_ENTITY_ID).attributes
    assert (attrs["written"], attrs["written_lost"]) == (1, 0)

    await hass.services.async_call(DOMAIN, "unlink", {"mac": LAMP_MAC}, blocking=True)
    assert _connections(hass, lamp) == before
    assert LAMP_MAC not in store.written
    assert LAMP_MAC not in store.overrides


async def test_a_mac_the_device_declares_is_never_recorded_nor_removed(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Linking a MAC the device already declares writes nothing; unlink keeps it."""
    sc, entry = await _setup(hass, hass_storage)
    other = sc.devices["other_lamp"]
    await _link(hass, mac=TAKEN_MAC, device_id=other)
    assert TAKEN_MAC not in entry.runtime_data.store.written
    await hass.services.async_call(DOMAIN, "unlink", {"mac": TAKEN_MAC}, blocking=True)
    assert _mac(TAKEN_MAC) in _connections(hass, other)


async def test_linking_elsewhere_moves_the_written_mac(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A new device takes the MAC; a label alone takes it back off."""
    sc, entry = await _setup(hass, hass_storage)
    lamp, speaker = sc.devices["lamp"], sc.devices["speaker"]
    await _link(hass, mac=SPEAKER_MAC, device_id=lamp)
    await _link(hass, mac=SPEAKER_MAC, device_id=speaker)
    assert _mac(SPEAKER_MAC) not in _connections(hass, lamp)
    assert _mac(SPEAKER_MAC) in _connections(hass, speaker)
    assert entry.runtime_data.store.written[SPEAKER_MAC]["device_id"] == speaker

    await _link(hass, mac=SPEAKER_MAC, label="Guest speaker")
    assert _mac(SPEAKER_MAC) not in _connections(hass, speaker)
    assert SPEAKER_MAC not in entry.runtime_data.store.written


@pytest.mark.parametrize(
    ("device_key", "mac", "error"),
    [
        ("service", SPEAKER_MAC, "write_service"),
        ("router_client", SPEAKER_MAC, "write_router"),
        ("lamp", TAKEN_MAC, "write_collision"),
    ],
)
async def test_refused_writes_change_nothing(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    device_key: str,
    mac: str,
    error: str,
) -> None:
    """Service and router devices, and a MAC of the same integration, are refused."""
    sc, entry = await _setup(hass, hass_storage)
    device_id = sc.devices[device_key]
    before = _connections(hass, device_id)
    with pytest.raises(ServiceValidationError) as err:
        await _link(hass, mac=mac, device_id=device_id)
    assert err.value.translation_key == error
    if error == "write_collision":  # the message names the device that has the MAC
        assert (err.value.translation_placeholders or {}).get("other") == "Example Lamp Hall"
    assert _connections(hass, device_id) == before
    assert mac not in entry.runtime_data.store.written
    assert mac not in entry.runtime_data.store.overrides  # nothing stored either


async def test_bluetooth_mac_is_refused(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """Only network MACs are written."""
    sc, entry = await _setup(hass, hass_storage)
    with pytest.raises(ServiceValidationError) as err:
        async_write_mac(
            hass,
            entry.runtime_data.store,
            LAMP_MAC,
            sc.devices["lamp"],
            router_domains=frozenset({"tplink_router"}),
            bluetooth=True,
        )
    assert err.value.translation_key == "write_bluetooth"


async def test_option_off_writes_only_on_request(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Off by default; the action can still ask for it."""
    sc, entry = await _setup(hass, hass_storage, write=False)
    lamp, speaker = sc.devices["lamp"], sc.devices["speaker"]
    await _link(hass, mac=LAMP_MAC, device_id=lamp)
    assert _mac(LAMP_MAC) not in _connections(hass, lamp)
    await _link(hass, mac=SPEAKER_MAC, device_id=speaker, write_to_registry=True)
    assert _mac(SPEAKER_MAC) in _connections(hass, speaker)
    assert list(entry.runtime_data.store.written) == [SPEAKER_MAC]


async def test_revert_and_lost(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """Revert removes every written MAC and keeps the links; replaced ones are lost."""
    sc, entry = await _setup(hass, hass_storage)
    lamp, speaker = sc.devices["lamp"], sc.devices["speaker"]
    await _link(hass, mac=LAMP_MAC, device_id=lamp)
    await _link(hass, mac=SPEAKER_MAC, device_id=speaker)

    # The speaker's integration replaces its connections: the written MAC is lost
    dr.async_get(hass).async_update_device(speaker, new_connections=set())
    await entry.runtime_data.async_refresh()
    attrs = hass.states.get(SUMMARY_ENTITY_ID).attributes
    assert (attrs["written"], attrs["written_lost"]) == (2, 1)

    response = await hass.services.async_call(
        DOMAIN, "revert_written", {}, blocking=True, return_response=True
    )
    assert response == {"removed": 1}
    assert _mac(LAMP_MAC) not in _connections(hass, lamp)
    store = entry.runtime_data.store
    assert store.written == {}
    assert store.overrides[LAMP_MAC]["device_id"] == lamp


async def test_removing_the_integration_reverts(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Uninstalling MAC Registry takes its MACs back off the devices."""
    sc, entry = await _setup(hass, hass_storage)
    lamp = sc.devices["lamp"]
    await _link(hass, mac=LAMP_MAC, device_id=lamp)
    await entry.runtime_data.store.async_save()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _mac(LAMP_MAC) not in _connections(hass, lamp)


async def test_repair_flow_writes_and_keeps_links_on_refusal(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Accepted proposals are written; outside the action a refusal keeps the link."""
    sc, entry = await _setup(hass, hass_storage)
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, context={"issue_id": ISSUE_ORPHANS})
    lamp, speaker = sc.devices["lamp"], sc.devices["speaker"]
    await manager.async_configure(
        result["flow_id"],
        {"link": [f"{LAMP_MAC}|{lamp}", f"{SPEAKER_MAC}|{speaker}"]},
    )
    assert _mac(LAMP_MAC) in _connections(hass, lamp)
    assert _mac(SPEAKER_MAC) in _connections(hass, speaker)

    # The repair flow and the options do not stop on a refused write
    coordinator = entry.runtime_data
    service = sc.devices["service"]
    assert coordinator.async_set_link(CLIENT_MAC, service, None, strict=False) is None
    assert coordinator.store.overrides[CLIENT_MAC]["device_id"] == service
    assert CLIENT_MAC not in coordinator.store.written
