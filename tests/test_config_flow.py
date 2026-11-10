"""Config and options flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mac_registry.const import (
    CONF_IGNORE_RANDOM,
    CONF_ROUTER_INTEGRATIONS,
    CONF_SCAN_INTERVAL,
    CONF_USE_DHCP,
    DOMAIN,
)

from .common import async_setup_integration, async_setup_scenario


async def test_user_flow_single_instance(hass: HomeAssistant) -> None:
    """One confirmation step, then a single instance only."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "MAC Registry"
    assert result["options"][CONF_SCAN_INTERVAL] == 15
    await hass.async_block_till_done()

    again = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert again["type"] is FlowResultType.ABORT
    assert again["reason"] == "single_instance_allowed"


async def test_options_settings(hass: HomeAssistant) -> None:
    """Settings are saved and the entry reloads with them."""
    await async_setup_scenario(hass)
    entry = await async_setup_integration(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 30,
            CONF_ROUTER_INTEGRATIONS: ["tplink_router"],
            CONF_IGNORE_RANDOM: True,
            CONF_USE_DHCP: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SCAN_INTERVAL] == 30
    assert entry.options[CONF_IGNORE_RANDOM] is True
    coordinator = entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 1800
    assert "02:00:5e:00:53:40" not in coordinator.data.by_mac


async def test_options_overrides(hass: HomeAssistant) -> None:
    """Add then remove manual links, stored in .storage."""
    sc = await async_setup_scenario(hass)
    entry = await async_setup_integration(hass)

    async def open_step(step: str) -> dict:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        return await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": step}
        )

    result = await open_step("remove_override")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_overrides"

    result = await open_step("add_override")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"mac": "not a mac", "label": "x"}
    )
    assert result["errors"] == {"mac": "invalid_mac"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"mac": "00-00-5E-00-53-41"}
    )
    assert result["errors"] == {"base": "override_empty"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"mac": "00-00-5E-00-53-41", "device_id": sc.devices["hub"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    row = entry.runtime_data.data.by_mac["00:00:5e:00:53:41"]
    assert (row["confidence"], row["device_name"]) == ("manual", "Example Hub")
    assert entry.runtime_data.store.overrides["00:00:5e:00:53:41"]["device_id"] == sc.devices["hub"]

    result = await open_step("remove_override")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"remove": ["00:00:5e:00:53:41"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.store.overrides == {}
    assert entry.runtime_data.data.by_mac["00:00:5e:00:53:41"]["confidence"] == "unmatched"
