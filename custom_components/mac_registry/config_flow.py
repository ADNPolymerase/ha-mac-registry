"""Config and options flows for MAC Registry."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, selector
import probatio as vol

from .const import (
    CONF_IGNORE_RANDOM,
    CONF_ROUTER_INTEGRATIONS,
    CONF_SCAN_INTERVAL,
    CONF_USE_DHCP,
    DEFAULT_IGNORE_RANDOM,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_DHCP,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    NAME,
)
from .coordinator import MacRegistryCoordinator
from .matcher import normalize_mac

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    CONF_ROUTER_INTEGRATIONS: [],
    CONF_IGNORE_RANDOM: DEFAULT_IGNORE_RANDOM,
    CONF_USE_DHCP: DEFAULT_USE_DHCP,
}

CONF_MAC = "mac"
CONF_DEVICE_ID = "device_id"
CONF_LABEL = "label"
CONF_REMOVE = "remove"


class MacRegistryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single instance config flow (enforced by single_config_entry)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm the setup, nothing to configure."""
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={}, options=DEFAULT_OPTIONS)
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> MacRegistryOptionsFlow:
        """Return the options flow."""
        return MacRegistryOptionsFlow()


class MacRegistryOptionsFlow(OptionsFlow):
    """Settings and manual overrides (stored in .storage/mac_registry)."""

    def _coordinator(self) -> MacRegistryCoordinator | None:
        return getattr(self.config_entry, "runtime_data", None)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "add_override", "remove_override"],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Refresh interval, router integrations, random MACs, DHCP source."""
        if user_input is not None:
            data = {**DEFAULT_OPTIONS, **self.config_entry.options, **user_input}
            data[CONF_SCAN_INTERVAL] = int(data[CONF_SCAN_INTERVAL])
            return self.async_create_entry(data=data)

        options = {**DEFAULT_OPTIONS, **self.config_entry.options}
        coordinator = self._coordinator()
        platforms = set(coordinator.router_platforms if coordinator else ())
        platforms.update(options[CONF_ROUTER_INTEGRATIONS])
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=options[CONF_SCAN_INTERVAL]
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(
                    CONF_ROUTER_INTEGRATIONS,
                    default=list(options[CONF_ROUTER_INTEGRATIONS]),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sorted(platforms),
                        multiple=True,
                        custom_value=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_IGNORE_RANDOM, default=options[CONF_IGNORE_RANDOM]
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_USE_DHCP, default=options[CONF_USE_DHCP]
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_add_override(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Link a MAC to a device or give it a free label."""
        if (coordinator := self._coordinator()) is None:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            mac = normalize_mac(user_input.get(CONF_MAC))
            device_id = user_input.get(CONF_DEVICE_ID) or None
            label = (user_input.get(CONF_LABEL) or "").strip() or None
            if mac is None:
                errors[CONF_MAC] = "invalid_mac"
            elif device_id is None and label is None:
                errors["base"] = "override_empty"
            elif device_id is not None and dr.async_get(self.hass).async_get(device_id) is None:
                errors[CONF_DEVICE_ID] = "unknown_device"
            else:
                coordinator.store.set_override(mac, device_id, label)
                await coordinator.async_refresh()
                return self.async_create_entry(data=dict(self.config_entry.options))

        schema = vol.Schema(
            {
                vol.Required(CONF_MAC): selector.TextSelector(),
                vol.Optional(CONF_DEVICE_ID): selector.DeviceSelector(
                    selector.DeviceSelectorConfig()
                ),
                vol.Optional(CONF_LABEL): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="add_override",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_remove_override(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one or more overrides."""
        if (coordinator := self._coordinator()) is None:
            return self.async_abort(reason="not_loaded")
        overrides = coordinator.store.overrides
        if not overrides:
            return self.async_abort(reason="no_overrides")
        if user_input is not None:
            for mac in user_input.get(CONF_REMOVE, []):
                coordinator.store.remove_override(mac)
            await coordinator.async_refresh()
            return self.async_create_entry(data=dict(self.config_entry.options))

        dev_reg = dr.async_get(self.hass)
        choices = []
        for mac, value in sorted(overrides.items()):
            target = value.get("label") or ""
            if (device_id := value.get("device_id")) and (device := dev_reg.async_get(device_id)):
                target = device.name_by_user or device.name or device_id
            choices.append(selector.SelectOptionDict(value=mac, label=f"{mac} → {target}"))
        schema = vol.Schema(
            {
                vol.Required(CONF_REMOVE): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=choices, multiple=True)
                )
            }
        )
        return self.async_show_form(step_id="remove_override", data_schema=schema)
