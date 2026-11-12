"""MAC Registry: map MAC, IP and hostname to Home Assistant devices.

Read-only towards the rest of Home Assistant and without any network traffic.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
import probatio as vol

from .const import (
    ATTR_DEVICE_ID,
    ATTR_FORMAT,
    ATTR_LABEL,
    ATTR_MAC,
    ATTR_PATH,
    ATTR_QUERY,
    DOMAIN,
    EXPORT_FORMATS,
    ISSUE_DUPLICATES,
    ISSUE_ORPHANS,
    SERVICE_EXPORT,
    SERVICE_LINK,
    SERVICE_LOOKUP,
    SERVICE_REFRESH,
    SERVICE_UNLINK,
)
from .coordinator import MacRegistryConfigEntry, MacRegistryCoordinator
from .export import (
    ExportPathError,
    render_csv,
    render_markdown,
    resolve_export_path,
    write_text,
)
from .matcher import normalize_mac
from .store import MacRegistryStore

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

LOOKUP_SCHEMA = vol.Schema({vol.Required(ATTR_QUERY): cv.string})
LINK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MAC): cv.string,
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_LABEL): cv.string,
    }
)
UNLINK_SCHEMA = vol.Schema({vol.Required(ATTR_MAC): cv.string})
EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PATH): cv.string,
        vol.Optional(ATTR_FORMAT): vol.In(EXPORT_FORMATS),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services and WebSocket commands once."""
    _async_register_services(hass)
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_lookup)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MacRegistryConfigEntry) -> bool:
    """Set up MAC Registry from a config entry."""
    store = MacRegistryStore(hass)
    await store.async_load()
    coordinator = MacRegistryCoordinator(hass, entry, store)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.async_setup_listeners()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MacRegistryConfigEntry) -> bool:
    """Unload a config entry, saving the history first."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.store.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: MacRegistryConfigEntry) -> None:
    """Delete stored history and overrides when the integration is removed."""
    await MacRegistryStore(hass).async_remove()
    for issue_id in (ISSUE_ORPHANS, ISSUE_DUPLICATES):
        ir.async_delete_issue(hass, DOMAIN, issue_id)


async def _async_options_updated(hass: HomeAssistant, entry: MacRegistryConfigEntry) -> None:
    """Reload to apply new options."""
    await hass.config_entries.async_reload(entry.entry_id)


# ----------------------------------------------------------------------
# Helpers


@callback
def _get_coordinator(hass: HomeAssistant) -> MacRegistryCoordinator | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator: MacRegistryCoordinator = entry.runtime_data
        return coordinator
    return None


@callback
def _require_coordinator(hass: HomeAssistant) -> MacRegistryCoordinator:
    if (coordinator := _get_coordinator(hass)) is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return coordinator


# ----------------------------------------------------------------------
# Services


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    async def async_lookup(call: ServiceCall) -> ServiceResponse:
        coordinator = _require_coordinator(hass)
        return await coordinator.async_lookup(call.data[ATTR_QUERY])

    async def async_export(call: ServiceCall) -> ServiceResponse:
        coordinator = _require_coordinator(hass)
        table = await coordinator.async_ensure_data()
        generated_at = dt_util.now().isoformat()
        response: dict[str, Any] = {
            "generated_at": generated_at,
            "count": len(table.rows),
            "summary": coordinator.summary,
            "rows": table.rows,
        }
        if path := call.data.get(ATTR_PATH):
            try:
                target, fmt = await hass.async_add_executor_job(
                    resolve_export_path,
                    hass.config.config_dir,
                    path,
                    call.data.get(ATTR_FORMAT),
                )
            except ExportPathError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key=err.reason,
                    translation_placeholders={"path": path},
                ) from err
            content = (
                render_csv(table.rows)
                if fmt == "csv"
                else render_markdown(table.rows, coordinator.summary, generated_at)
            )
            await hass.async_add_executor_job(write_text, target, content)
            response["written"] = str(target)
            response["format"] = fmt
        return response

    async def async_refresh(call: ServiceCall) -> None:
        await _require_coordinator(hass).async_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_LOOKUP,
        async_lookup,
        schema=LOOKUP_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT,
        async_export,
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, async_refresh)

    def _mac(call: ServiceCall) -> str:
        if (mac := normalize_mac(call.data[ATTR_MAC])) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_mac",
                translation_placeholders={"mac": str(call.data[ATTR_MAC])},
            )
        return mac

    async def async_link(call: ServiceCall) -> None:
        coordinator = _require_coordinator(hass)
        mac = _mac(call)
        device_id = call.data.get(ATTR_DEVICE_ID) or None
        label = (call.data.get(ATTR_LABEL) or "").strip() or None
        if device_id is None and label is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="link_empty")
        if device_id is not None and dr.async_get(hass).async_get(device_id) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_device",
                translation_placeholders={"device_id": device_id},
            )
        await coordinator.async_link(mac, device_id, label)

    async def async_unlink(call: ServiceCall) -> None:
        await _require_coordinator(hass).async_unlink(_mac(call))

    hass.services.async_register(DOMAIN, SERVICE_LINK, async_link, schema=LINK_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_UNLINK, async_unlink, schema=UNLINK_SCHEMA)


# ----------------------------------------------------------------------
# WebSocket


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "mac_registry/list"})
@websocket_api.async_response
async def ws_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the whole table."""
    if (coordinator := _get_coordinator(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "MAC Registry is not loaded")
        return
    table = await coordinator.async_ensure_data()
    connection.send_result(
        msg["id"],
        {
            "rows": table.rows,
            "summary": coordinator.summary,
            "last_built": coordinator.last_built,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "mac_registry/lookup", vol.Required("query"): str}
)
@websocket_api.async_response
async def ws_lookup(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Resolve a MAC, IP, hostname or log line."""
    if (coordinator := _get_coordinator(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "MAC Registry is not loaded")
        return
    connection.send_result(msg["id"], await coordinator.async_lookup(msg["query"]))
