"""Coordinator for MAC Registry."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import TrackStates, async_track_state_change_filtered
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_SOURCE_TYPE,
    ATTR_TRACKER_CONNECTION,
    ATTR_TRACKER_HOST_NAME,
    ATTR_TRACKER_IP,
    ATTR_TRACKER_MAC,
    CONF_IGNORE_RANDOM,
    CONF_ROUTER_INTEGRATIONS,
    CONF_SCAN_INTERVAL,
    CONF_USE_DHCP,
    CONFIG_ENTRY_HOST_KEYS,
    DEBOUNCE_COOLDOWN,
    DEFAULT_IGNORE_RANDOM,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_DHCP,
    DOMAIN,
    EVENT_IP_CHANGED,
    EVENT_NEW_MAC,
    ISSUE_DUPLICATES,
    ISSUE_LIST_LIMIT,
    ISSUE_ORPHANS,
    ORPHAN_MIN_DAYS,
    SOURCE_TYPE_ROUTER,
    STATE_HOME,
    TRACKER_DOMAIN,
)
from .matcher import (
    DeviceInput,
    DhcpInput,
    EntryInput,
    Snapshot,
    Table,
    TrackerInput,
    build_table,
    is_repair_orphan,
    lookup,
    normalize_ip,
    normalize_mac,
    split_host,
    summarize,
)
from .store import MacRegistryStore

_LOGGER = logging.getLogger(__name__)

_RELEVANT_TRACKER_ATTRS = (
    ATTR_SOURCE_TYPE,
    ATTR_TRACKER_MAC,
    ATTR_TRACKER_IP,
    ATTR_TRACKER_HOST_NAME,
)

type MacRegistryConfigEntry = ConfigEntry[MacRegistryCoordinator]


class MacRegistryCoordinator(DataUpdateCoordinator[Table]):
    """Build and keep the MAC table up to date. Read-only towards HA."""

    config_entry: MacRegistryConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MacRegistryConfigEntry,
        store: MacRegistryStore,
    ) -> None:
        """Initialise the coordinator."""
        minutes = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
            always_update=True,
        )
        self.store = store
        self.summary: dict[str, Any] = {}
        self.dhcp_available = False
        self.last_built: str | None = None
        self.router_platforms: set[str] = set()
        self._event_debouncer: Debouncer[Any] = Debouncer(
            hass,
            _LOGGER,
            cooldown=DEBOUNCE_COOLDOWN,
            immediate=False,
            function=self.async_refresh,
        )

    # ------------------------------------------------------------------
    # Event driven refresh

    @callback
    def async_setup_listeners(self) -> None:
        """Listen to registry and router tracker changes (debounced)."""
        entry = self.config_entry
        entry.async_on_unload(
            self.hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._async_registry_changed
            )
        )
        entry.async_on_unload(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._async_registry_changed
            )
        )
        tracker = async_track_state_change_filtered(
            self.hass,
            TrackStates(False, set(), {TRACKER_DOMAIN}),
            self._async_tracker_changed,
        )
        entry.async_on_unload(tracker.async_remove)
        entry.async_on_unload(self._event_debouncer.async_shutdown)

    @callback
    def _async_registry_changed(self, _event: Event[Any]) -> None:
        self.async_schedule_refresh()

    @callback
    def _async_tracker_changed(self, event: Event[EventStateChangedData]) -> None:
        """Only react to changes that matter (not online_time, rssi...)."""
        old = event.data["old_state"]
        new = event.data["new_state"]
        if old is None or new is None:
            self.async_schedule_refresh()
            return
        if (
            old.attributes.get(ATTR_SOURCE_TYPE) != SOURCE_TYPE_ROUTER
            and new.attributes.get(ATTR_SOURCE_TYPE) != SOURCE_TYPE_ROUTER
        ):
            return
        if (old.state == STATE_HOME) != (new.state == STATE_HOME) or any(
            old.attributes.get(key) != new.attributes.get(key) for key in _RELEVANT_TRACKER_ATTRS
        ):
            self.async_schedule_refresh()

    @callback
    def async_schedule_refresh(self) -> None:
        """Ask for a debounced refresh."""
        self._event_debouncer.async_schedule_call()

    # ------------------------------------------------------------------
    # Snapshot (event loop, in-memory reads only)

    @callback
    def _build_snapshot(self) -> Snapshot:
        hass = self.hass
        options = self.config_entry.options
        router_filter = set(options.get(CONF_ROUTER_INTEGRATIONS) or [])
        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)

        # Router trackers
        trackers: list[TrackerInput] = []
        platforms: set[str] = set()
        for state in hass.states.async_all(TRACKER_DOMAIN):
            attrs = state.attributes
            if attrs.get(ATTR_SOURCE_TYPE) != SOURCE_TYPE_ROUTER:
                continue
            reg_entry = ent_reg.async_get(state.entity_id)
            platform = reg_entry.platform if reg_entry else None
            if platform:
                platforms.add(platform)
            if (mac := normalize_mac(attrs.get(ATTR_TRACKER_MAC))) is None:
                continue
            if router_filter and platform not in router_filter:
                continue
            host = attrs.get(ATTR_TRACKER_HOST_NAME)
            connection = attrs.get(ATTR_TRACKER_CONNECTION)
            trackers.append(
                TrackerInput(
                    entity_id=state.entity_id,
                    platform=platform,
                    mac=mac,
                    ip=normalize_ip(attrs.get(ATTR_TRACKER_IP)),
                    hostname=host.strip() if isinstance(host, str) and host.strip() else None,
                    online=state.state == STATE_HOME,
                    connection=str(connection) if connection is not None else None,
                    device_id=reg_entry.device_id if reg_entry else None,
                )
            )
        self.router_platforms = platforms
        # Integrations named in the options stay router integrations even when
        # none of their trackers has a state (router unreachable, restart...)
        router_domains = platforms | router_filter

        # Entities per device (child devices are folded into their parent)
        entities: dict[str, list[str]] = defaultdict(list)
        for reg_entry in ent_reg.entities.values():
            if reg_entry.device_id is None:
                continue
            entities[reg_entry.device_id].append(reg_entry.entity_id)
        for child in dev_reg.child_devices:
            parent = getattr(child, "parent_device_id", None)
            if parent and child.id in entities:
                entities[parent].extend(entities[child.id])

        entry_domains = {
            entry.entry_id: entry.domain for entry in hass.config_entries.async_entries()
        }

        devices: list[DeviceInput] = []
        for device in dev_reg.devices:
            macs: list[str] = []
            bt_macs: list[str] = []
            for conn_type, value in device.connections:
                if conn_type == dr.CONNECTION_NETWORK_MAC:
                    target = macs
                elif conn_type == dr.CONNECTION_BLUETOOTH:
                    target = bt_macs
                else:  # zigbee IEEE, upnp... are not MACs
                    continue
                if (mac := normalize_mac(value)) and mac not in target:
                    target.append(mac)
            device_entities = entities.get(device.id, [])
            domain = entry_domains.get(device.config_entry_id)
            if not macs and not bt_macs and not device_entities:
                continue
            devices.append(
                DeviceInput(
                    device_id=device.id,
                    name=device.name_by_user or device.name or device.id,
                    domain=domain,
                    config_entry_id=device.config_entry_id,
                    manufacturer=device.manufacturer,
                    model=device.model,
                    macs=tuple(macs),
                    bt_macs=tuple(bt_macs),
                    entities=tuple(sorted(device_entities)),
                    disabled=device.disabled,
                    # Since Home Assistant 2026.10 a router integration registers
                    # its own device, with the same MAC, for each client another
                    # integration knows. It only carries device trackers (or
                    # nothing when the tracker is gone), unlike the router itself.
                    router_client=domain in router_domains
                    and all(eid.startswith(f"{TRACKER_DOMAIN}.") for eid in device_entities),
                )
            )

        # Config entries: unique_id and the host keys only, never anything else
        entries: list[EntryInput] = []
        for entry in hass.config_entries.async_entries():
            if entry.domain == DOMAIN:
                continue
            unique_mac = (
                normalize_mac(entry.unique_id) if isinstance(entry.unique_id, str) else None
            )
            host_ip: str | None = None
            host_name: str | None = None
            for key in CONFIG_ENTRY_HOST_KEYS:
                ip_value, name_value = split_host(entry.data.get(key))
                host_ip = host_ip or ip_value
                host_name = host_name or name_value
            if unique_mac or host_ip or host_name:
                entries.append(
                    EntryInput(
                        entry_id=entry.entry_id,
                        domain=entry.domain,
                        unique_mac=unique_mac,
                        host_ip=host_ip,
                        host_name=host_name,
                    )
                )

        return Snapshot(
            devices=devices,
            trackers=trackers,
            entries=entries,
            dhcp=self._dhcp_snapshot(),
            overrides={mac: dict(value) for mac, value in self.store.overrides.items()},
            router_domains=frozenset(router_domains),
            ignore_random=bool(options.get(CONF_IGNORE_RANDOM, DEFAULT_IGNORE_RANDOM)),
        )

    @callback
    def _dhcp_snapshot(self) -> list[DhcpInput]:
        """Read HA's DHCP discovery cache through its public helper."""
        self.dhcp_available = False
        if not self.config_entry.options.get(CONF_USE_DHCP, DEFAULT_USE_DHCP):
            return []
        if "dhcp" not in self.hass.config.components:
            return []
        try:
            from homeassistant.components.dhcp import (
                async_discovered_service_info,
            )

            infos = async_discovered_service_info(self.hass)
        except Exception:
            _LOGGER.debug("DHCP discovery cache unavailable, source disabled")
            return []
        self.dhcp_available = True
        result: list[DhcpInput] = []
        for info in infos:
            if (mac := normalize_mac(info.macaddress)) is None:
                continue
            result.append(
                DhcpInput(
                    mac=mac,
                    ip=normalize_ip(info.ip),
                    hostname=info.hostname or None,
                )
            )
        return result

    # ------------------------------------------------------------------
    # Refresh

    async def _async_update_data(self) -> Table:
        """Build the table: snapshot in the loop, matching in an executor."""
        snapshot = self._build_snapshot()
        first_pass = not self.store.initialized
        observation = self.store.observe(snapshot.trackers)
        snapshot.history = self.store.history_copy()
        table = await self.hass.async_add_executor_job(build_table, snapshot)
        self.summary = summarize(table)
        self.last_built = dt_util.utcnow().isoformat()

        if not first_pass:
            for mac in observation.new_macs:
                self.hass.bus.async_fire(EVENT_NEW_MAC, _event_data(mac, table))
        for change in observation.ip_changes:
            data = _event_data(change.mac, table)
            data.update(old_ip=change.old_ip, new_ip=change.new_ip, ip=change.new_ip)
            self.hass.bus.async_fire(EVENT_IP_CHANGED, data)

        self._async_update_issues(table)
        return table

    @callback
    def _async_update_issues(self, table: Table) -> None:
        orphans = [row for row in table.rows if is_repair_orphan(row)]
        if orphans:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_ORPHANS,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_ORPHANS,
                translation_placeholders={
                    "count": str(len(orphans)),
                    "days": str(ORPHAN_MIN_DAYS),
                    "macs": _issue_list(
                        f"{row['mac']} ({row['hostname'] or row['ip'] or '?'})" for row in orphans
                    ),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ORPHANS)

        if table.duplicates:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_DUPLICATES,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_DUPLICATES,
                translation_placeholders={
                    "count": str(len(table.duplicates)),
                    "macs": _issue_list(
                        f"{dup['mac']}: "
                        + ", ".join(
                            f"{dev['device_name']} ({dev['integration']})" for dev in dup["devices"]
                        )
                        for dup in table.duplicates
                    ),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_DUPLICATES)

    # ------------------------------------------------------------------
    # Queries

    async def async_ensure_data(self) -> Table:
        """Return the current table, building it if needed."""
        if self.data is None:
            await self.async_refresh()
        return self.data if self.data is not None else Table()

    async def async_lookup(self, query: str) -> dict[str, Any]:
        """Resolve a MAC, IP, hostname or log line."""
        return lookup(await self.async_ensure_data(), query)


def _issue_list(lines: Any) -> str:
    items = list(lines)
    shown = [f"- {line}" for line in items[:ISSUE_LIST_LIMIT]]
    if len(items) > ISSUE_LIST_LIMIT:
        shown.append(f"- … (+{len(items) - ISSUE_LIST_LIMIT})")
    return "\n".join(shown)


def _event_data(mac: str, table: Table) -> dict[str, Any]:
    row = table.by_mac.get(mac) or {}
    return {
        "mac": mac,
        "ip": row.get("ip"),
        "hostname": row.get("hostname"),
        "tracker": row.get("tracker"),
        "device_id": row.get("device_id"),
        "device_name": row.get("device_name"),
        "integration": row.get("integration"),
        "confidence": row.get("confidence"),
        "random": row.get("random"),
    }
