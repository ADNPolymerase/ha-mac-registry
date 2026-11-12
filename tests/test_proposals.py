"""Proposals, the orphan repair flow and the link / unlink actions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest

from custom_components.mac_registry.const import (
    DOMAIN,
    ISSUE_ORPHANS,
    STORAGE_KEY,
    SUMMARY_ENTITY_ID,
)

from .common import (
    add_device,
    add_entry,
    add_tracker,
    async_setup_integration,
    async_setup_scenario,
)

TV_MAC = "00:00:5e:00:53:c1"
CAMERA_MAC = "00:00:5e:00:53:c2"
LAPTOP_MAC = "00:00:5e:00:53:41"


async def _setup(hass: HomeAssistant, hass_storage: dict[str, Any]) -> Any:
    """Scenario plus devices without MAC that the router sees under a close name."""
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
                for mac in (TV_MAC, CAMERA_MAC, LAPTOP_MAC)
            },
            "overrides": {},
        },
    }
    sc = await async_setup_scenario(hass)
    apple = add_entry(hass, sc, "apple_tv", host="192.0.2.150")
    add_device(hass, sc, "tv", apple, "Apple TV Salon", entities=("media_player.apple_tv",))
    arlo = add_entry(hass, sc, "arlo")
    add_device(hass, sc, "cam", arlo, "Avant", manufacturer="Arlo", entities=("camera.avant",))
    add_tracker(hass, sc, "apple_tv", TV_MAC, "192.0.2.151", "Apple TV")
    add_tracker(hass, sc, "arlo_avant", CAMERA_MAC, "192.0.2.152", "Arlo-Avant")
    await hass.async_block_till_done()
    entry = await async_setup_integration(hass)
    return sc, entry


async def test_proposals_are_exposed(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """Rows, sensor and repair placeholders carry the proposals."""
    sc, entry = await _setup(hass, hass_storage)
    table = entry.runtime_data.data
    tv = table.by_mac[TV_MAC]
    assert tv["device_id"] is None
    assert tv["proposals"][0]["device_id"] == sc.devices["tv"]
    assert tv["proposals"][0]["reason"] == "name"
    assert table.by_mac[CAMERA_MAC]["proposals"][0]["device_name"] == "Avant"
    assert table.by_mac[LAPTOP_MAC]["proposals"] == []

    attrs = hass.states.get(SUMMARY_ENTITY_ID).attributes
    assert attrs["proposals"] == 2
    assert {p["mac"] for p in attrs["proposal_list"]} == {TV_MAC, CAMERA_MAC}

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ORPHANS)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.translation_placeholders["proposals"] == "2"


async def test_repair_flow_links_and_labels(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """One screen: accept proposals, label the rest, the issue goes away."""
    sc, entry = await _setup(hass, hass_storage)
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None

    result = await manager.async_init(DOMAIN, context={"issue_id": ISSUE_ORPHANS})
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["proposals"] == "2"
    assert TV_MAC in result["description_placeholders"]["macs"]
    schema = {str(key): key for key in result["data_schema"].schema}
    assert set(schema) == {"link", "known"}

    result = await manager.async_configure(
        result["flow_id"],
        {
            "link": [f"{TV_MAC}|{sc.devices['tv']}", f"{CAMERA_MAC}|{sc.devices['cam']}"],
            # The TV is in both lists: the accepted proposal wins
            "known": [LAPTOP_MAC, TV_MAC],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    overrides = entry.runtime_data.store.overrides
    assert overrides[TV_MAC] == {"device_id": sc.devices["tv"], "label": None}
    assert overrides[CAMERA_MAC] == {"device_id": sc.devices["cam"], "label": None}
    assert overrides[LAPTOP_MAC] == {"device_id": None, "label": "Example-Laptop"}
    table = entry.runtime_data.data
    assert table.by_mac[TV_MAC]["confidence"] == "manual"
    assert table.by_mac[TV_MAC]["device_name"] == "Apple TV Salon"
    assert table.by_mac[TV_MAC]["proposals"] == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ORPHANS) is None


async def test_link_and_unlink_actions(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """mac_registry.link / unlink store manual links; bad input is refused."""
    sc, entry = await _setup(hass, hass_storage)

    await hass.services.async_call(
        DOMAIN, "link", {"mac": "00-00-5E-00-53-C1", "device_id": sc.devices["tv"]}, blocking=True
    )
    row = entry.runtime_data.data.by_mac[TV_MAC]
    assert (row["confidence"], row["device_name"]) == ("manual", "Apple TV Salon")

    await hass.services.async_call(
        DOMAIN, "link", {"mac": LAPTOP_MAC, "label": "Office laptop"}, blocking=True
    )
    assert entry.runtime_data.data.by_mac[LAPTOP_MAC]["label"] == "Office laptop"

    for data, key in (
        ({"mac": "not a mac", "label": "x"}, "invalid_mac"),
        ({"mac": LAPTOP_MAC}, "link_empty"),
        ({"mac": LAPTOP_MAC, "device_id": "missing"}, "unknown_device"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, "link", data, blocking=True)
        assert err.value.translation_key == key

    await hass.services.async_call(DOMAIN, "unlink", {"mac": TV_MAC}, blocking=True)
    row = entry.runtime_data.data.by_mac[TV_MAC]
    assert row["confidence"] == "unmatched"
    assert row["proposals"][0]["device_id"] == sc.devices["tv"]
