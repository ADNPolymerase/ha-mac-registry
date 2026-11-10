"""Integration tests with a simulated Home Assistant inventory."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import sys
import types
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.mac_registry.const import (
    DOMAIN,
    EVENT_IP_CHANGED,
    EVENT_NEW_MAC,
    ISSUE_DUPLICATES,
    ISSUE_ORPHANS,
    STORAGE_KEY,
    SUMMARY_ENTITY_ID,
)
from custom_components.mac_registry.diagnostics import async_get_config_entry_diagnostics

from .common import (
    LOG_PLUG,
    LOG_SHELLY,
    LOG_VACUUM,
    SECRET,
    add_tracker,
    async_setup_integration,
    async_setup_scenario,
    router_attrs,
)


async def _lookup(hass: HomeAssistant, query: str) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN, "lookup", {"query": query}, blocking=True, return_response=True
    )


async def test_lookup_resolves_the_three_log_lines(hass: HomeAssistant) -> None:
    """The log lines of the specification resolve to the right device."""
    await async_setup_scenario(hass)
    await async_setup_integration(hass)

    shelly = await _lookup(hass, LOG_SHELLY)
    assert shelly["count"] == 1
    row = shelly["results"][0]
    assert row["device_name"] == "Example Shelly"
    assert row["integration"] == "shelly"
    assert row["confidence"] == "tracker+registry"
    assert row["mac"] == "00:00:5e:00:53:01"
    assert row["ip"] == "192.0.2.31"
    assert row["tracker"] == "device_tracker.shelly1g3"
    assert row["manufacturer"] == "Shelly"
    assert row["entities"] == ["sensor.example_shelly_power", "switch.example_shelly"]
    assert row["matched_by"] == "mac"

    plug = await _lookup(hass, LOG_PLUG)
    assert [r["device_name"] for r in plug["results"]] == ["Example Plug"]
    assert plug["results"][0]["matched_by"] == "ip"
    assert plug["extracted"]["ips"] == ["192.0.2.207"]

    vacuum = await _lookup(hass, LOG_VACUUM)
    assert [r["device_name"] for r in vacuum["results"]] == ["Example Vacuum"]
    assert vacuum["results"][0]["confidence"] == "ip"
    assert vacuum["results"][0]["entities"] == ["vacuum.example_vacuum"]
    assert vacuum["results"][0]["tracker"] == "device_tracker.00_00_5e_00_53_8a"


async def test_table_levels_and_exclusions(hass: HomeAssistant) -> None:
    """Hostname, IP-only, duplicates, Zigbee and router-only devices."""
    await async_setup_scenario(hass)
    entry = await async_setup_integration(hass)
    table = entry.runtime_data.data

    esp = table.by_mac["00:00:5e:00:53:50"]
    assert (esp["confidence"], esp["device_name"]) == ("hostname", "Example Sensor")
    assert table.by_mac["00:00:5e:00:53:60"]["confidence"] == "registry"
    assert table.by_mac["00:00:5e:00:53:70"]["duplicate"] is True
    laptop = table.by_mac["00:00:5e:00:53:41"]
    assert laptop["confidence"] == "unmatched"
    assert laptop["router_device_id"] is not None  # router-created device ignored
    assert table.by_mac["02:00:5e:00:53:40"]["random"] is True
    # Zigbee IEEE never becomes a MAC
    assert all(not row["mac"].startswith("0x") for row in table.rows)
    # Fan only known by IP in its config entry: lookup gives it as candidate
    fan = await _lookup(hass, "192.0.2.99")
    assert fan["candidates"][0]["device_name"] == "Example Fan"


async def test_summary_sensor(hass: HomeAssistant) -> None:
    """The summary sensor exposes counters and capped lists."""
    await async_setup_scenario(hass)
    await async_setup_integration(hass)
    state = hass.states.get(SUMMARY_ENTITY_ID)
    assert state is not None
    attrs = state.attributes
    assert int(state.state) == attrs["known"]
    assert attrs["orphans"] == 2  # laptop + random phone
    # Hub and heater: Bluetooth (ESP) and Zigbee are excluded
    assert attrs["unseen_devices"] == 2
    assert {d["mac"] for d in attrs["unseen_device_list"]} == {
        "00:00:5e:00:53:60",
        "00:00:5e:00:53:70",
    }
    assert attrs["duplicates"] == 1
    assert attrs["random"] == 1
    assert {o["mac"] for o in attrs["orphan_list"]} == {"00:00:5e:00:53:41", "02:00:5e:00:53:40"}
    assert len(json.dumps(dict(attrs))) < 16000
    assert er.async_get(hass).async_get(SUMMARY_ENTITY_ID) is not None


async def test_no_secret_is_exposed(hass: HomeAssistant, hass_ws_client: Any) -> None:
    """Only host / ip_address are read from config entries."""
    await async_setup_scenario(hass)
    entry = await async_setup_integration(hass)
    export = await hass.services.async_call(
        DOMAIN, "export", {}, blocking=True, return_response=True
    )
    lookup = await _lookup(hass, "192.0.2.31 192.0.2.198 192.0.2.99")
    diag = await async_get_config_entry_diagnostics(hass, entry)
    for payload in (export, lookup, diag):
        assert SECRET not in json.dumps(payload, default=str)


async def test_export_writes_files_inside_config(hass: HomeAssistant, tmp_path: Path) -> None:
    """Markdown and CSV exports, confined to the config directory."""
    await async_setup_scenario(hass)
    await async_setup_integration(hass)
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "exports").mkdir()

    result = await hass.services.async_call(
        DOMAIN, "export", {"path": "mac_registry.md"}, blocking=True, return_response=True
    )
    assert result["format"] == "markdown"
    content = (tmp_path / "mac_registry.md").read_text(encoding="utf-8")
    assert "| 00:00:5e:00:53:01 | 192.0.2.31 |" in content
    assert "Example Shelly" in content
    assert result["count"] == len(result["rows"])

    await hass.services.async_call(
        DOMAIN, "export", {"path": "exports/mac_registry.csv"}, blocking=True, return_response=True
    )
    csv_text = (tmp_path / "exports" / "mac_registry.csv").read_text(encoding="utf-8")
    assert csv_text.splitlines()[0].startswith("mac,ip,hostname,device_name")

    outside = tmp_path.parent / "outside.md"
    for bad, key in (
        ("../outside.md", "path_outside_config"),
        (str(outside), "path_outside_config"),
        ("/etc/mac_registry.md", "path_outside_config"),
        (".storage/mac_registry.md", "path_hidden"),
        ("mac_registry.txt", "path_extension"),
        ("missing/mac_registry.md", "path_no_directory"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN, "export", {"path": bad}, blocking=True, return_response=True
            )
        assert err.value.translation_key == key
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "export",
            {"path": "mac_registry.md", "format": "csv"},
            blocking=True,
            return_response=True,
        )
    assert not outside.exists()

    # A symlink pointing outside is refused as well
    (tmp_path / "link.md").symlink_to(outside)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "export", {"path": "link.md"}, blocking=True, return_response=True
        )
    assert not outside.exists()


async def test_websocket_commands(hass: HomeAssistant, hass_ws_client: Any) -> None:
    """mac_registry/list and mac_registry/lookup."""
    await async_setup_scenario(hass)
    await async_setup_integration(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "mac_registry/list"})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["summary"]["known"] == len(msg["result"]["rows"])

    await client.send_json_auto_id({"type": "mac_registry/lookup", "query": LOG_VACUUM})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["results"][0]["device_name"] == "Example Vacuum"


async def test_events_and_persistence(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """No event on the first pass; new MAC and IP change afterwards; history survives."""
    sc = await async_setup_scenario(hass)
    new_events = async_capture_events(hass, EVENT_NEW_MAC)
    ip_events = async_capture_events(hass, EVENT_IP_CHANGED)
    entry = await async_setup_integration(hass)
    coordinator = entry.runtime_data
    assert new_events == []

    # New device on the network and DHCP change of the plug
    add_tracker(hass, sc, "example_tablet", "00:00:5e:00:53:90", "192.0.2.90", "Example-Tablet")
    hass.states.async_set(
        "device_tracker.example_plug",
        "home",
        router_attrs("00:00:5e:00:53:20", "192.0.2.208", "Example-Plug"),
    )
    # Unrelated attribute change only: must not matter
    hass.states.async_set(
        "device_tracker.example_camera",
        "home",
        router_attrs("00:00:5e:00:53:14", "192.0.2.14", "Example-Camera", online_time=99.0),
    )
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()

    assert [e.data["mac"] for e in new_events] == ["00:00:5e:00:53:90"]
    assert new_events[0].data["hostname"] == "Example-Tablet"
    assert len(ip_events) == 1
    assert ip_events[0].data["mac"] == "00:00:5e:00:53:20"
    assert ip_events[0].data["old_ip"] == "192.0.2.207"
    assert ip_events[0].data["new_ip"] == "192.0.2.208"
    assert ip_events[0].data["device_name"] == "Example Plug"

    # The old IP still resolves through the history
    old = await _lookup(hass, LOG_PLUG)
    assert old["results"][0]["matched_by"] == "previous_ip"

    # Unload saves the history; a new setup reads it back
    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = hass_storage[STORAGE_KEY]["data"]
    assert stored["initialized"] is True
    assert stored["macs"]["00:00:5e:00:53:20"]["ips"] == ["192.0.2.208", "192.0.2.207"]

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not coordinator
    row = entry.runtime_data.data.by_mac["00:00:5e:00:53:20"]
    assert row["last_ips"] == ["192.0.2.208", "192.0.2.207"]
    # Restart is not a first pass: the known MACs fire nothing new
    assert [e.data["mac"] for e in new_events] == ["00:00:5e:00:53:90"]


async def test_repairs(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """Grouped issue for repeated orphans, and one for duplicates."""
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
                for mac in ("00:00:5e:00:53:41", "02:00:5e:00:53:40")
            },
            "overrides": {},
        },
    }
    await async_setup_scenario(hass)
    entry = await async_setup_integration(hass)
    registry = ir.async_get(hass)
    orphans = registry.async_get_issue(DOMAIN, ISSUE_ORPHANS)
    assert orphans is not None
    assert orphans.translation_placeholders["count"] == "1"  # random phone excluded
    assert "00:00:5e:00:53:41" in orphans.translation_placeholders["macs"]
    duplicates = registry.async_get_issue(DOMAIN, ISSUE_DUPLICATES)
    assert duplicates is not None
    assert "Example Heater (cloud)" in duplicates.translation_placeholders["macs"]

    # A label removes the MAC from the orphan issue
    entry.runtime_data.store.set_override("00:00:5e:00:53:41", None, "Office laptop")
    await entry.runtime_data.async_refresh()
    assert registry.async_get_issue(DOMAIN, ISSUE_ORPHANS) is None


async def test_registry_change_triggers_refresh(hass: HomeAssistant) -> None:
    """A new device in the registry is picked up after the debounce delay."""
    sc = await async_setup_scenario(hass)
    entry = await async_setup_integration(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=sc.entries["shelly"].entry_id,
        identifiers={("shelly", "laptop")},
        connections={(dr.CONNECTION_NETWORK_MAC, "00:00:5e:00:53:41")},
        name="Example Laptop",
    )
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    row = entry.runtime_data.data.by_mac["00:00:5e:00:53:41"]
    assert (row["confidence"], row["device_name"]) == ("tracker+registry", "Example Laptop")


async def test_diagnostics_are_masked(hass: HomeAssistant) -> None:
    """No MAC, IP or hostname in diagnostics."""
    await async_setup_scenario(hass)
    entry = await async_setup_integration(hass)
    entry.runtime_data.store.set_override("00:00:5e:00:53:41", None, "Office laptop")
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag)
    for needle in ("00:00:5e", "00_00_5e", "00005e", "192.0.2.", "Example-Camera", "shelly1g3-"):
        assert needle not in text
    assert diag["summary"]["known"] > 0
    assert diag["override_count"] == 1


async def test_dhcp_source(hass: HomeAssistant) -> None:
    """The DHCP cache is used through its public helper when loaded."""
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

    await async_setup_scenario(hass)
    hass.config.components.add("dhcp")
    infos = [DhcpServiceInfo(ip="192.0.2.61", hostname="example-hub", macaddress="00005e005360")]
    # The real component needs aiodhcpwatcher (present on HAOS, not in tests)
    fake_dhcp = types.ModuleType("homeassistant.components.dhcp")
    fake_dhcp.async_discovered_service_info = lambda hass: infos  # type: ignore[attr-defined]
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "homeassistant.components.dhcp", fake_dhcp)
        entry = await async_setup_integration(hass)
    coordinator = entry.runtime_data
    assert coordinator.dhcp_available is True
    hub = coordinator.data.by_mac["00:00:5e:00:53:60"]
    assert hub["ip"] == "192.0.2.61"
    assert "dhcp" in hub["sources"]
    # Still "never seen by the router": DHCP does not count
    assert hub["seen_by_router"] is False
    assert hass.states.get(SUMMARY_ENTITY_ID).attributes["dhcp_source"] == "active"


async def test_dhcp_unavailable_is_disabled_cleanly(hass: HomeAssistant) -> None:
    """A DHCP component that cannot be used never breaks the integration."""
    await async_setup_scenario(hass)
    hass.config.components.add("dhcp")
    broken = types.ModuleType("homeassistant.components.dhcp")  # no helper inside
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "homeassistant.components.dhcp", broken)
        entry = await async_setup_integration(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.dhcp_available is False
    assert hass.states.get(SUMMARY_ENTITY_ID).attributes["dhcp_source"] == "inactive"


async def test_service_without_entry(hass: HomeAssistant) -> None:
    """Services fail cleanly when the integration is not loaded."""
    entry = await async_setup_integration(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await _lookup(hass, "192.0.2.1")
