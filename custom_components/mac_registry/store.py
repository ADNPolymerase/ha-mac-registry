"""Persistent storage for MAC Registry (``.storage/mac_registry``)."""

from __future__ import annotations

from collections.abc import Iterable
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    HISTORY_RETENTION_DAYS,
    MAX_DAYS,
    MAX_IPS,
    SAVE_DELAY,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .matcher import TrackerInput, normalize_mac


@dataclass(slots=True, frozen=True)
class IpChange:
    """An IP change of a known MAC."""

    mac: str
    old_ip: str
    new_ip: str


@dataclass(slots=True)
class Observation:
    """What changed while recording a refresh."""

    new_macs: list[str]
    ip_changes: list[IpChange]


def _empty() -> dict[str, Any]:
    return {"initialized": False, "macs": {}, "overrides": {}}


class MacRegistryStore:
    """History per MAC and manual overrides.

    Layout::

        {
          "initialized": true,
          "macs": {"00:00:5e:00:53:01": {
              "first_seen": iso, "last_seen": iso, "hostname": str | None,
              "ips": [most recent first], "days": ["2026-11-10", ...]}},
          "overrides": {"00:00:5e:00:53:02": {"device_id": str | None,
                                              "label": str | None}}
        }

    Only the event loop mutates the data; the matcher receives copies.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the store."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, private=True, atomic_writes=True
        )
        self.data: dict[str, Any] = _empty()

    async def async_load(self) -> None:
        """Load data from disk, tolerating a missing or partial file."""
        loaded = await self._store.async_load()
        data = _empty()
        if isinstance(loaded, dict):
            data["initialized"] = bool(loaded.get("initialized"))
            if isinstance(loaded.get("macs"), dict):
                data["macs"] = {
                    mac: entry
                    for raw, entry in loaded["macs"].items()
                    if (mac := normalize_mac(raw)) and isinstance(entry, dict)
                }
            if isinstance(loaded.get("overrides"), dict):
                data["overrides"] = {
                    mac: entry
                    for raw, entry in loaded["overrides"].items()
                    if (mac := normalize_mac(raw)) and isinstance(entry, dict)
                }
        self.data = data

    # ------------------------------------------------------------------
    # History

    @property
    def initialized(self) -> bool:
        """Return True once a first refresh has been recorded."""
        return bool(self.data["initialized"])

    @callback
    def history_copy(self) -> dict[str, dict[str, Any]]:
        """Return a deep copy of the history for the matcher."""
        return copy.deepcopy(self.data["macs"])

    @callback
    def observe(self, trackers: Iterable[TrackerInput], now: datetime | None = None) -> Observation:
        """Record the router trackers that are currently online."""
        now = now or dt_util.now()
        stamp = now.isoformat()
        today = now.date().isoformat()
        macs: dict[str, dict[str, Any]] = self.data["macs"]
        result = Observation(new_macs=[], ip_changes=[])
        for tracker in trackers:
            if not tracker.online:
                continue
            entry = macs.get(tracker.mac)
            if entry is None:
                entry = macs[tracker.mac] = {
                    "first_seen": stamp,
                    "last_seen": stamp,
                    "hostname": tracker.hostname,
                    "ips": [],
                    "days": [],
                }
                result.new_macs.append(tracker.mac)
            ips: list[str] = entry.setdefault("ips", [])
            if tracker.ip:
                if ips and ips[0] != tracker.ip:
                    result.ip_changes.append(IpChange(tracker.mac, ips[0], tracker.ip))
                if tracker.ip in ips:
                    ips.remove(tracker.ip)
                ips.insert(0, tracker.ip)
                del ips[MAX_IPS:]
            days: list[str] = entry.setdefault("days", [])
            if today not in days:
                days.append(today)
                del days[:-MAX_DAYS]
            entry["last_seen"] = stamp
            if tracker.hostname:
                entry["hostname"] = tracker.hostname
        self.data["initialized"] = True
        self.prune(now)
        self.schedule_save()
        return result

    @callback
    def prune(self, now: datetime) -> None:
        """Drop MACs not seen for a year (overrides are always kept)."""
        limit = now - timedelta(days=HISTORY_RETENTION_DAYS)
        macs: dict[str, dict[str, Any]] = self.data["macs"]
        for mac in list(macs):
            last = dt_util.parse_datetime(macs[mac].get("last_seen") or "")
            if last is not None and last < limit and mac not in self.data["overrides"]:
                del macs[mac]

    # ------------------------------------------------------------------
    # Overrides

    @property
    def overrides(self) -> dict[str, dict[str, Any]]:
        """Return the manual overrides (read-only use)."""
        return self.data["overrides"]

    @callback
    def set_override(self, mac: str, device_id: str | None, label: str | None) -> None:
        """Create or replace a manual override."""
        self.data["overrides"][mac] = {
            "device_id": device_id or None,
            "label": (label or "").strip() or None,
        }
        self.schedule_save()

    @callback
    def remove_override(self, mac: str) -> bool:
        """Remove a manual override."""
        removed = self.data["overrides"].pop(mac, None) is not None
        if removed:
            self.schedule_save()
        return removed

    # ------------------------------------------------------------------
    # Saving

    @callback
    def schedule_save(self) -> None:
        """Save later, grouping writes (flushed on shutdown by HA)."""
        self._store.async_delay_save(lambda: self.data, SAVE_DELAY)

    async def async_save(self) -> None:
        """Save now."""
        await self._store.async_save(self.data)

    async def async_remove(self) -> None:
        """Delete the file (integration removed)."""
        await self._store.async_remove()
