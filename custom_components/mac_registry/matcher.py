"""Pure matching logic for MAC Registry.

Nothing in this module touches Home Assistant objects: the coordinator builds a
plain snapshot in the event loop and :func:`build_table` runs in an executor.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import ipaddress
import re
from typing import Any

from .const import (
    CONF_DISCOVERY,
    CONF_HOSTNAME,
    CONF_IP,
    CONF_MANUAL,
    CONF_REGISTRY,
    CONF_TRACKER_REGISTRY,
    CONF_UNMATCHED,
    CONFIDENCE_ORDER,
    ORPHAN_MIN_DAYS,
    SRC_DHCP,
    SRC_HISTORY,
    SRC_MANUAL,
    SRC_REGISTRY,
    SRC_ROUTER,
)

# --------------------------------------------------------------------------
# MAC normalisation
# --------------------------------------------------------------------------

_HEX12 = re.compile(r"^[0-9a-f]{12}$")
_SEPARATED = re.compile(r"^[0-9a-f]{1,2}([:\-_ ])(?:[0-9a-f]{1,2}\1){4}[0-9a-f]{1,2}$")
_DOTTED = re.compile(r"^[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}$")
_INVALID_MACS = frozenset({"000000000000", "ffffffffffff"})


def normalize_mac(value: Any) -> str | None:
    """Return a MAC as ``aa:bb:cc:dd:ee:ff`` or ``None`` if it is not a MAC.

    Accepts ``AA-BB-CC-DD-EE-FF``, ``aa:bb:cc:dd:ee:ff``, ``aa_bb_...``,
    ``aabbccddeeff``, ``aabb.ccdd.eeff`` and single-digit octets (``a:b:c:d:e:f``).
    Zigbee IEEE addresses (8 bytes, ``0x...``) are rejected.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if not raw or raw.startswith("0x"):
        return None
    if _HEX12.match(raw):
        hex12 = raw
    elif _DOTTED.match(raw):
        hex12 = raw.replace(".", "")
    elif match := _SEPARATED.match(raw):
        hex12 = "".join(part.zfill(2) for part in raw.split(match.group(1)))
    else:
        return None
    if hex12 in _INVALID_MACS:
        return None
    return ":".join(hex12[i : i + 2] for i in range(0, 12, 2))


def is_random_mac(mac: str) -> bool:
    """Return True for a locally administered (randomised) MAC: x2, x6, xA, xE."""
    return len(mac) >= 2 and int(mac[1], 16) & 0x2 == 0x2


def mac_hex(mac: str) -> str:
    """Return the 12 hex digits of a normalised MAC."""
    return mac.replace(":", "")


# --------------------------------------------------------------------------
# Hostnames
# --------------------------------------------------------------------------

_HOST_FULL_MAC = re.compile(r"(?<![0-9a-f])([0-9a-f]{12})(?![0-9a-f])")
_HOST_SUFFIX_PATTERNS = (
    re.compile(r"^esp[_-]([0-9a-f]{6})$"),  # ESP_XXXXXX (Espressif default)
    re.compile(r"^tasmota[_-]([0-9a-f]{6})(?:[_-]\d{1,4})?$"),  # tasmota-XXXXXX-NNNN
    re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*[_-]([0-9a-f]{6})$"),  # name-XXXXXX
)
GENERIC_HOSTNAMES = frozenset({"espressif", "esp32", "esp8266", "localhost", "unknown"})


def normalize_hostname(value: Any) -> str | None:
    """Lower-case a hostname and drop its domain (``.local``, ``.lan``...)."""
    if not isinstance(value, str):
        return None
    host = value.strip().strip(".").lower()
    if not host or host == "*":
        return None
    if _is_ip(host):
        return None
    host = host.split(".", 1)[0]
    return host or None


@dataclass(slots=True, frozen=True)
class HostnameHint:
    """MAC information carried by a hostname."""

    full_mac: str | None = None
    suffix: str | None = None  # last 6 hex digits (3 bytes)


def hostname_hint(hostname: str | None) -> HostnameHint:
    """Extract a full MAC or a 3-byte suffix from a hostname.

    ``shelly1g3-00005e005301`` -> full MAC, ``ESP_005301`` -> suffix ``005301``.
    Generic names such as ``espressif`` carry no MAC information.
    """
    host = normalize_hostname(hostname)
    if not host or host in GENERIC_HOSTNAMES:
        return HostnameHint()
    if (mac := normalize_mac(host)) is not None and not mac_hex(mac).isdigit():
        return HostnameHint(full_mac=mac)
    # 12 plain digits are a timestamp or a serial, not a MAC
    if (
        (match := _HOST_FULL_MAC.search(host))
        and not match.group(1).isdigit()
        and (mac := normalize_mac(match.group(1)))
    ):
        return HostnameHint(full_mac=mac)
    for pattern in _HOST_SUFFIX_PATTERNS:
        if (match := pattern.match(host)) and any(c.isdigit() for c in match.group(1)):
            return HostnameHint(suffix=match.group(1))
    return HostnameHint()


# --------------------------------------------------------------------------
# IP / host values
# --------------------------------------------------------------------------


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def normalize_ip(value: Any) -> str | None:
    """Return a compressed IP string or ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return ipaddress.ip_address(value.strip()).compressed
    except ValueError:
        return None


def split_host(value: Any) -> tuple[str | None, str | None]:
    """Split a config entry ``host`` value into ``(ip, hostname)``.

    Handles ``192.0.2.5``, ``192.0.2.5:8080``, ``http://192.0.2.5/`` and
    ``device.local``. The value itself is never logged.
    """
    if not isinstance(value, str) or not value.strip():
        return None, None
    host = value.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].split("@")[-1]
    if host.startswith("["):  # [ipv6]:port
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    if (ip := normalize_ip(host)) is not None:
        return ip, None
    return None, normalize_hostname(host)


# --------------------------------------------------------------------------
# Free text (log line) extraction
# --------------------------------------------------------------------------

_TEXT_MAC_SEP = re.compile(
    r"(?<![0-9a-f:\-_])([0-9a-f]{2}([:\-_])(?:[0-9a-f]{2}\2){4}[0-9a-f]{2})(?![0-9a-f:\-_]?[0-9a-f])",
    re.IGNORECASE,
)
_TEXT_MAC_DOT = re.compile(
    r"(?<![0-9a-f.])([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})(?![0-9a-f.])", re.IGNORECASE
)
_TEXT_MAC_HEX = re.compile(r"(?<![0-9a-z])([0-9a-f]{12})(?![0-9a-z])", re.IGNORECASE)
_TEXT_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d]|\.\d)")
_TEXT_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")


@dataclass(slots=True)
class Extracted:
    """Identifiers found in a free text."""

    macs: list[str] = field(default_factory=list)
    ips: list[str] = field(default_factory=list)
    hostnames: list[str] = field(default_factory=list)
    suffixes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        """Return a JSON friendly dict."""
        return {
            "macs": list(self.macs),
            "ips": list(self.ips),
            "hostnames": list(self.hostnames),
            "mac_suffixes": list(self.suffixes),
        }


def _add(target: list[str], value: str | None) -> None:
    if value and value not in target:
        target.append(value)


def extract_identifiers(text: str) -> Extracted:
    """Extract MACs, IPv4 addresses and hostname candidates from a log line."""
    found = Extracted()
    if not isinstance(text, str):
        return found
    for match in _TEXT_MAC_SEP.finditer(text):
        _add(found.macs, normalize_mac(match.group(1)))
    for match in _TEXT_MAC_DOT.finditer(text):
        _add(found.macs, normalize_mac(match.group(1)))
    for match in _TEXT_MAC_HEX.finditer(text):
        if not match.group(1).isdigit():  # 12 digits is a timestamp, not a MAC
            _add(found.macs, normalize_mac(match.group(1)))
    for match in _TEXT_IPV4.finditer(text):
        _add(found.ips, normalize_ip(match.group(1)))
    for match in _TEXT_TOKEN.finditer(text):
        token = match.group(0).strip("._-")
        if not token or _is_ip(token):
            continue
        if (host := normalize_hostname(token)) is None or len(host) < 2:
            continue
        _add(found.hostnames, host)
        hint = hostname_hint(host)
        _add(found.macs, hint.full_mac)
        _add(found.suffixes, hint.suffix)
    # MAC-derived tracker ids (device_tracker.00_00_5e_00_53_8a) are caught by
    # the separated pattern above, which accepts "_".
    return found


# --------------------------------------------------------------------------
# Snapshot model
# --------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class DeviceInput:
    """A device of the device registry."""

    device_id: str
    name: str
    domain: str | None
    config_entry_id: str | None
    manufacturer: str | None = None
    model: str | None = None
    macs: tuple[str, ...] = ()
    bt_macs: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    disabled: bool = False
    router_client: bool = False  # created by a router integration for a tracker


@dataclass(slots=True, frozen=True)
class TrackerInput:
    """A router based device_tracker."""

    entity_id: str
    platform: str | None
    mac: str
    ip: str | None
    hostname: str | None
    online: bool
    connection: str | None = None
    device_id: str | None = None


@dataclass(slots=True, frozen=True)
class EntryInput:
    """The matching relevant parts of a config entry (never secrets)."""

    entry_id: str
    domain: str
    unique_mac: str | None = None
    host_ip: str | None = None
    host_name: str | None = None


@dataclass(slots=True, frozen=True)
class DhcpInput:
    """An entry of the DHCP discovery cache."""

    mac: str
    ip: str | None
    hostname: str | None


@dataclass(slots=True)
class Snapshot:
    """Everything the matcher needs, as plain data."""

    devices: list[DeviceInput] = field(default_factory=list)
    trackers: list[TrackerInput] = field(default_factory=list)
    entries: list[EntryInput] = field(default_factory=list)
    dhcp: list[DhcpInput] = field(default_factory=list)
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: dict[str, dict[str, Any]] = field(default_factory=dict)
    router_domains: frozenset[str] = frozenset()
    ignore_random: bool = False


@dataclass(slots=True)
class Table:
    """The result of a matching pass, with lookup indexes."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    by_mac: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_ip: dict[str, list[str]] = field(default_factory=dict)
    by_previous_ip: dict[str, list[str]] = field(default_factory=dict)
    by_hostname: dict[str, list[str]] = field(default_factory=dict)
    ip_devices: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    hostname_devices: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    duplicates: list[dict[str, Any]] = field(default_factory=list)


def _device_ref(device: DeviceInput) -> dict[str, Any]:
    return {
        "device_id": device.device_id,
        "device_name": device.name,
        "integration": device.domain,
    }


def _sort_devices(devices: list[DeviceInput]) -> list[DeviceInput]:
    """Enabled first, then the device with the most entities, then by name."""
    return sorted(
        devices, key=lambda d: (d.disabled, -len(d.entities), d.name.lower(), d.device_id)
    )


def _same_integration_groups(devices: list[DeviceInput]) -> list[DeviceInput]:
    """Return the devices that share their integration with another one."""
    by_domain: dict[str | None, list[DeviceInput]] = defaultdict(list)
    for device in devices:
        by_domain[device.domain].append(device)
    return [d for group in by_domain.values() if len(group) > 1 for d in group]


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def build_table(snap: Snapshot) -> Table:
    """Build the MAC table from a snapshot."""
    devices_by_id = {d.device_id: d for d in snap.devices}
    router_device_by_mac: dict[str, DeviceInput] = {}
    mac_index: dict[str, list[DeviceInput]] = defaultdict(list)
    bt_index: dict[str, list[DeviceInput]] = defaultdict(list)
    suffix_index: dict[str, set[str]] = defaultdict(set)
    devices_by_entry: dict[str, list[DeviceInput]] = defaultdict(list)

    for device in snap.devices:
        is_router = device.router_client
        for mac in device.macs:
            if is_router:
                router_device_by_mac.setdefault(mac, device)
            else:
                mac_index[mac].append(device)
        if is_router:
            continue
        for mac in device.bt_macs:
            bt_index[mac].append(device)
        for mac in (*device.macs, *device.bt_macs):
            suffix_index[mac_hex(mac)[6:]].add(device.device_id)
        if device.config_entry_id:
            devices_by_entry[device.config_entry_id].append(device)

    entries_by_mac: dict[str, list[EntryInput]] = defaultdict(list)
    entries_by_ip: dict[str, list[EntryInput]] = defaultdict(list)
    entries_by_host: dict[str, list[EntryInput]] = defaultdict(list)
    for entry in snap.entries:
        if entry.domain in snap.router_domains:
            continue
        if entry.unique_mac:
            entries_by_mac[entry.unique_mac].append(entry)
        if entry.host_ip:
            entries_by_ip[entry.host_ip].append(entry)
        if entry.host_name:
            entries_by_host[entry.host_name].append(entry)

    def entry_devices(entries: list[EntryInput]) -> list[DeviceInput]:
        found: dict[str, DeviceInput] = {}
        for entry in entries:
            for device in devices_by_entry.get(entry.entry_id, ()):
                found[device.device_id] = device
        return _sort_devices(list(found.values()))

    trackers: dict[str, TrackerInput] = {}
    for tracker in sorted(snap.trackers, key=lambda t: (not t.online, t.entity_id)):
        trackers.setdefault(tracker.mac, tracker)
    dhcp = {item.mac: item for item in snap.dhcp}

    universe = (
        set(trackers)
        | set(dhcp)
        | set(mac_index)
        | set(bt_index)
        | set(snap.overrides)
        | set(snap.history)
    )

    table = Table()
    for mac in universe:
        tracker = trackers.get(mac)
        dhcp_item = dhcp.get(mac)
        hist = snap.history.get(mac, {})
        override = snap.overrides.get(mac) or {}
        registry_devices = _sort_devices(mac_index.get(mac, []))
        is_bt = not registry_devices and mac in bt_index
        if is_bt:
            registry_devices = _sort_devices(bt_index[mac])
        random = is_random_mac(mac)
        if snap.ignore_random and random and not override and not registry_devices:
            continue

        sources: list[str] = []
        router_device = router_device_by_mac.get(mac)
        if registry_devices:
            sources.append(SRC_REGISTRY)
        if tracker or router_device:
            sources.append(SRC_ROUTER)
        if dhcp_item:
            sources.append(SRC_DHCP)
        if hist:
            sources.append(SRC_HISTORY)
        if override:
            sources.append(SRC_MANUAL)

        ip = (tracker.ip if tracker else None) or (dhcp_item.ip if dhcp_item else None)
        hostname = (tracker.hostname if tracker else None) or (
            dhcp_item.hostname if dhcp_item else None
        )
        if hostname is None:
            hostname = hist.get("hostname")
        # A router-side device means the router integration tracks this MAC,
        # even while its tracker has no state (disabled, restored, unavailable)
        seen_by_router = (
            tracker is not None or router_device is not None or bool(hist.get("last_seen"))
        )

        device: DeviceInput | None = None
        confidence = CONF_UNMATCHED
        candidates: list[DeviceInput] = []
        linked: list[DeviceInput] = []
        duplicates: list[DeviceInput] = []
        label = override.get("label") or None

        if override and (override.get("device_id") in devices_by_id or label is not None):
            device = devices_by_id.get(override.get("device_id") or "")
            confidence = CONF_MANUAL
        elif registry_devices:
            # Several integrations may declare the same MAC (one device per
            # config entry since Home Assistant 2026.10): they are all linked.
            # Only two devices of the same integration are suspicious.
            device = registry_devices[0]
            linked = registry_devices[1:]
            duplicates = _same_integration_groups(registry_devices)
            confidence = CONF_TRACKER_REGISTRY if seen_by_router else CONF_REGISTRY
        else:
            found = entry_devices(entries_by_mac.get(mac, []))
            if len(found) == 1:
                device, confidence = found[0], CONF_DISCOVERY
            else:
                candidates.extend(found)
                host_found = _hostname_candidates(
                    hostname,
                    mac,
                    mac_index,
                    bt_index,
                    suffix_index,
                    devices_by_id,
                    entry_devices(entries_by_host.get(hostname or "", [])),
                )
                if len(host_found) == 1 and not found:
                    device, confidence = host_found[0], CONF_HOSTNAME
                else:
                    candidates.extend(host_found)
                    ip_found = entry_devices(entries_by_ip.get(ip or "", []))
                    if len(ip_found) == 1 and not candidates:
                        device, confidence = ip_found[0], CONF_IP
                    else:
                        candidates.extend(ip_found)

        if override and registry_devices:
            linked = [
                d for d in registry_devices if device is None or d.device_id != device.device_id
            ]
        router_trackers = (
            [e for e in router_device.entities if e.startswith("device_tracker.")]
            if router_device
            else []
        )
        unique_candidates = {c.device_id: c for c in candidates}
        row: dict[str, Any] = {
            "mac": mac,
            "mac_type": "bluetooth" if is_bt else "mac",
            "random": random,
            "ip": ip,
            "hostname": hostname,
            "online": bool(tracker and tracker.online),
            "tracker": tracker.entity_id
            if tracker
            else (router_trackers[0] if router_trackers else None),
            "tracker_platform": tracker.platform if tracker else None,
            "connection": tracker.connection if tracker else None,
            "router_device_id": router_device.device_id if router_device else None,
            "device_id": device.device_id if device else None,
            "device_name": device.name if device else None,
            "integration": device.domain if device else None,
            "manufacturer": device.manufacturer if device else None,
            "model": device.model if device else None,
            "entities": list(device.entities) if device else [],
            "label": label,
            "confidence": confidence,
            "sources": sources,
            "linked_devices": [{**_device_ref(d), "entities": list(d.entities)} for d in linked],
            "duplicate": bool(duplicates),
            "duplicate_devices": [_device_ref(d) for d in duplicates],
            "candidates": [_device_ref(c) for c in unique_candidates.values()],
            "seen_by_router": seen_by_router,
            "first_seen": hist.get("first_seen"),
            "last_seen": hist.get("last_seen"),
            "last_ips": list(hist.get("ips", [])),
            "days_seen": len(hist.get("days", [])),
        }
        table.rows.append(row)

    rank = {name: index for index, name in enumerate(CONFIDENCE_ORDER)}
    table.rows.sort(
        key=lambda r: (
            rank[r["confidence"]],
            (r["device_name"] or r["label"] or r["hostname"] or "~").lower(),
            r["mac"],
        )
    )

    for row in table.rows:
        mac = row["mac"]
        table.by_mac[mac] = row
        if row["ip"]:
            table.by_ip.setdefault(row["ip"], []).append(mac)
        for old_ip in row["last_ips"]:
            if old_ip != row["ip"]:
                table.by_previous_ip.setdefault(old_ip, []).append(mac)
        if host := normalize_hostname(row["hostname"]):
            table.by_hostname.setdefault(host, []).append(mac)
        if row["duplicate"]:
            table.duplicates.append(
                {"mac": mac, "devices": [dict(d) for d in row["duplicate_devices"]]}
            )

    for ip_value, entries in entries_by_ip.items():
        table.ip_devices[ip_value] = [_device_ref(d) for d in entry_devices(entries)]
    for host_value, entries in entries_by_host.items():
        table.hostname_devices[host_value] = [_device_ref(d) for d in entry_devices(entries)]
    return table


def _hostname_candidates(
    hostname: str | None,
    mac: str,
    mac_index: dict[str, list[DeviceInput]],
    bt_index: dict[str, list[DeviceInput]],
    suffix_index: dict[str, set[str]],
    devices_by_id: dict[str, DeviceInput],
    host_entry_devices: list[DeviceInput],
) -> list[DeviceInput]:
    """Return the devices a hostname points to."""
    found: dict[str, DeviceInput] = {d.device_id: d for d in host_entry_devices}
    hint = hostname_hint(hostname)
    if hint.full_mac and hint.full_mac != mac:
        for device in (*mac_index.get(hint.full_mac, ()), *bt_index.get(hint.full_mac, ())):
            found[device.device_id] = device
    if hint.suffix:
        for device_id in suffix_index.get(hint.suffix, ()):
            found[device_id] = devices_by_id[device_id]
    return _sort_devices(list(found.values()))


# --------------------------------------------------------------------------
# Summary and lookup
# --------------------------------------------------------------------------


def is_orphan(row: dict[str, Any]) -> bool:
    """Seen by the router, linked to no HA device and not labelled."""
    return row["seen_by_router"] and not row["device_id"] and not row["label"]


def is_unseen_device(row: dict[str, Any]) -> bool:
    """HA device MAC (not Bluetooth) never seen by a router tracker."""
    return (
        row["mac_type"] == "mac"
        and row["confidence"] == CONF_REGISTRY
        and not row["seen_by_router"]
    )


def is_repair_orphan(row: dict[str, Any]) -> bool:
    """Orphan worth a repair: non random, seen on enough distinct days."""
    return is_orphan(row) and not row["random"] and row["days_seen"] >= ORPHAN_MIN_DAYS


def summarize(table: Table) -> dict[str, Any]:
    """Return counters for the summary sensor."""
    rows = table.rows
    by_confidence = dict.fromkeys(CONFIDENCE_ORDER, 0)
    for row in rows:
        by_confidence[row["confidence"]] += 1
    return {
        "known": len(rows),
        "matched": sum(1 for r in rows if r["device_id"]),
        "router_linked": sum(1 for r in rows if r["device_id"] and r["router_device_id"]),
        "labelled": sum(1 for r in rows if r["label"] and not r["device_id"]),
        "orphans": sum(1 for r in rows if is_orphan(r)),
        "unseen_devices": sum(1 for r in rows if is_unseen_device(r)),
        "random": sum(1 for r in rows if r["random"]),
        "online": sum(1 for r in rows if r["online"]),
        "duplicates": len(table.duplicates),
        "by_confidence": by_confidence,
    }


def lookup(table: Table, query: str) -> dict[str, Any]:
    """Resolve a MAC, IP, hostname or raw log line to table rows."""
    extracted = extract_identifiers(query)
    results: list[dict[str, Any]] = []
    seen_macs: set[str] = set()
    candidates: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, str]] = set()
    unknown: list[str] = []

    def add_row(mac: str, matched_by: str, value: str) -> None:
        if mac in seen_macs or mac not in table.by_mac:
            return
        seen_macs.add(mac)
        results.append({**table.by_mac[mac], "matched_by": matched_by, "matched_value": value})

    def add_candidates(refs: list[dict[str, Any]], matched_by: str, value: str) -> bool:
        added = False
        for ref in refs:
            key = (ref["device_id"], value)
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            candidates.append({**ref, "matched_by": matched_by, "matched_value": value})
            added = True
        return added

    for mac in extracted.macs:
        if mac in table.by_mac:
            add_row(mac, "mac", mac)
        else:
            _add(unknown, mac)
    for ip in extracted.ips:
        hit = False
        for mac in table.by_ip.get(ip, []):
            add_row(mac, "ip", ip)
            hit = True
        if not hit:
            for mac in table.by_previous_ip.get(ip, []):
                add_row(mac, "previous_ip", ip)
                hit = True
        known_devices = {r["device_id"] for r in results if r["device_id"]}
        refs = [
            ref for ref in table.ip_devices.get(ip, []) if ref["device_id"] not in known_devices
        ]
        hit = add_candidates(refs, "config_entry_ip", ip) or hit
        if not hit:
            _add(unknown, ip)
    matched_hosts: list[str] = []
    for host in extracted.hostnames:
        for mac in table.by_hostname.get(host, []):
            add_row(mac, "hostname", host)
            _add(matched_hosts, host)
        if add_candidates(table.hostname_devices.get(host, []), "config_entry_host", host):
            _add(matched_hosts, host)
    # Every word of a log line is a hostname candidate: only report known ones
    extracted.hostnames = matched_hosts
    for suffix in extracted.suffixes:
        matches = [mac for mac in table.by_mac if mac_hex(mac).endswith(suffix)]
        for mac in matches:
            add_row(mac, "mac_suffix" if len(matches) == 1 else "mac_suffix_ambiguous", suffix)

    return {
        "query": query,
        "extracted": extracted.as_dict(),
        "count": len(results),
        "results": results,
        "candidates": candidates,
        "unknown": unknown,
    }
