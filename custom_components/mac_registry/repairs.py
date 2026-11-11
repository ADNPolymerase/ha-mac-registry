"""Repair flow for MAC Registry: link or label orphan MACs in one screen."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
import probatio as vol

from .const import DOMAIN, ISSUE_ORPHANS, ORPHAN_MIN_DAYS
from .coordinator import MacRegistryCoordinator, issue_list
from .matcher import is_orphan, is_repair_orphan

CONF_LINK = "link"
CONF_KNOWN = "known"
_SEP = "|"


def _coordinator(hass: HomeAssistant) -> MacRegistryCoordinator | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator: MacRegistryCoordinator = entry.runtime_data
        return coordinator
    return None


def _client_name(row: dict[str, Any]) -> str:
    return row["hostname"] or row["ip"] or row["mac"]


class OrphanMacsRepairFlow(RepairsFlow):
    """Accept proposed devices, or mark MACs as having no Home Assistant device."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        """Start the flow (the repairs manager passes the issue id here)."""
        return await self.async_step_links()

    async def async_step_links(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        """Show proposals and orphans, then store the chosen manual links."""
        if (coordinator := _coordinator(self.hass)) is None or coordinator.data is None:
            return self.async_abort(reason="not_loaded")
        orphans = [r for r in coordinator.data.rows if is_orphan(r) and not r["random"]]

        if user_input is not None:
            linked: set[str] = set()
            for value in user_input.get(CONF_LINK, []):
                mac, _, device_id = value.partition(_SEP)
                if mac in linked or not device_id:
                    continue
                coordinator.store.set_override(mac, device_id, None)
                linked.add(mac)
            names = {r["mac"]: _client_name(r) for r in orphans}
            for mac in user_input.get(CONF_KNOWN, []):
                if mac not in linked:
                    coordinator.store.set_override(mac, None, names.get(mac, mac))
            await coordinator.async_refresh()
            return self.async_create_entry(data={})

        link_options = [
            selector.SelectOptionDict(
                value=f"{row['mac']}{_SEP}{prop['device_id']}",
                label=(
                    f"{_client_name(row)} → {prop['device_name']}"
                    + (f" ({prop['integration']})" if prop["integration"] else "")
                ),
            )
            for row in orphans
            for prop in row["proposals"]
        ]
        known_options = [
            selector.SelectOptionDict(value=row["mac"], label=f"{_client_name(row)} ({row['mac']})")
            for row in orphans
        ]
        if not known_options:
            return self.async_abort(reason="no_orphans")
        schema: dict[Any, Any] = {}
        if link_options:
            schema[vol.Optional(CONF_LINK, default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=link_options, multiple=True)
            )
        schema[vol.Optional(CONF_KNOWN, default=[])] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=known_options, multiple=True)
        )
        return self.async_show_form(
            step_id="links",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "orphans": str(len(orphans)),
                "proposals": str(len(link_options)),
                "days": str(ORPHAN_MIN_DAYS),
                "macs": issue_list(
                    f"{row['mac']} ({_client_name(row)})"
                    for row in orphans
                    if is_repair_orphan(row)
                ),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow of an issue."""
    if issue_id != ISSUE_ORPHANS:
        raise ValueError(f"Unknown issue {issue_id}")
    return OrphanMacsRepairFlow()
