"""Tests for the pure matching logic."""

from __future__ import annotations

import pytest

from custom_components.mac_registry.matcher import (
    DeviceInput,
    DhcpInput,
    EntryInput,
    Snapshot,
    TrackerInput,
    build_table,
    extract_identifiers,
    hostname_hint,
    is_random_mac,
    lookup,
    normalize_hostname,
    normalize_mac,
    split_host,
    summarize,
)

from .common import LOG_PLUG, LOG_SHELLY, LOG_VACUUM


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00-00-5E-00-53-14", "00:00:5e:00:53:14"),
        ("00:00:5e:00:53:14", "00:00:5e:00:53:14"),
        ("00005E005314", "00:00:5e:00:53:14"),
        ("0000.5e00.5314", "00:00:5e:00:53:14"),
        ("00_00_5e_00_53_8a", "00:00:5e:00:53:8a"),
        ("0:0:5e:0:53:14", "00:00:5e:00:53:14"),
        (" 00:00:5e:00:53:14 ", "00:00:5e:00:53:14"),
        ("0x00005eef10000001", None),  # Zigbee IEEE
        ("00:00:5e:ef:10:00:00:01", None),  # 8 bytes
        ("00:00:5e:00:53", None),
        ("00:00:5e-00:53:14", None),  # mixed separators
        ("zz:00:5e:00:53:14", None),
        ("000000000000", None),
        ("ff:ff:ff:ff:ff:ff", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_normalize_mac(raw: object, expected: str | None) -> None:
    """MACs are lower case with colons, everything else is rejected."""
    assert normalize_mac(raw) == expected


@pytest.mark.parametrize(
    ("mac", "random"),
    [
        ("02:00:5e:00:53:40", True),
        ("06:00:5e:00:53:40", True),
        ("0a:00:5e:00:53:40", True),
        ("0e:00:5e:00:53:40", True),
        ("00:00:5e:00:53:40", False),
        ("01:00:5e:00:53:40", False),
    ],
)
def test_random_mac(mac: str, random: bool) -> None:
    """The locally administered bit flags random MACs."""
    assert is_random_mac(mac) is random


@pytest.mark.parametrize(
    ("hostname", "full", "suffix"),
    [
        ("shelly1g3-00005e005301", "00:00:5e:00:53:01", None),
        ("shellyplus1pm-00005E005301.local", "00:00:5e:00:53:01", None),
        ("00005e005301", "00:00:5e:00:53:01", None),
        ("ESP_005301", None, "005301"),
        ("esp-005301", None, "005301"),
        ("tasmota-005301-1234", None, "005301"),
        ("example-plug-005301", None, "005301"),
        ("espressif", None, None),
        ("Example-Camera", None, None),
        ("example-cafeba", None, None),  # hex word without digit
        ("printer-202611101230", None, None),  # 12 digits: not a MAC
        (None, None, None),
    ],
)
def test_hostname_hint(hostname: str | None, full: str | None, suffix: str | None) -> None:
    """Known hostname patterns give a full MAC or a 3-byte suffix."""
    hint = hostname_hint(hostname)
    assert (hint.full_mac, hint.suffix) == (full, suffix)


def test_normalize_hostname_and_split_host() -> None:
    """Hostnames drop their domain, hosts are split into IP or name."""
    assert normalize_hostname("Example-Camera.lan.") == "example-camera"
    assert normalize_hostname("192.0.2.1") is None
    assert normalize_hostname("*") is None
    assert split_host("192.0.2.5") == ("192.0.2.5", None)
    assert split_host("192.0.2.5:8080") == ("192.0.2.5", None)
    assert split_host("http://user@192.0.2.5:8080/path") == ("192.0.2.5", None)
    assert split_host("example-sensor.local") == (None, "example-sensor")
    assert split_host("") == (None, None)
    assert split_host(None) == (None, None)


def test_extract_identifiers_from_log_lines() -> None:
    """MAC, IP and hostname are pulled out of raw log lines."""
    shelly = extract_identifiers(LOG_SHELLY)
    assert shelly.macs == ["00:00:5e:00:53:01"]
    assert "shelly1g3-00005e005301" in shelly.hostnames

    plug = extract_identifiers(LOG_PLUG)
    assert plug.ips == ["192.0.2.207"]
    assert plug.macs == []

    vacuum = extract_identifiers(LOG_VACUUM)
    assert vacuum.ips == ["192.0.2.198"]

    tracker = extract_identifiers("device_tracker.00_00_5e_00_53_8a at 202611101230")
    assert tracker.macs == ["00:00:5e:00:53:8a"]

    mixed = extract_identifiers("from 00-00-5E-00-53-14 / 0000.5e00.5315 via ESP_005316")
    assert mixed.macs == ["00:00:5e:00:53:14", "00:00:5e:00:53:15"]
    assert mixed.suffixes == ["005316"]


def _device(device_id: str, **kwargs: object) -> DeviceInput:
    base: dict[str, object] = {
        "device_id": device_id,
        "name": device_id.title(),
        "domain": "example",
        "config_entry_id": f"entry_{device_id}",
    }
    base.update(kwargs)
    return DeviceInput(**base)  # type: ignore[arg-type]


def _tracker(
    mac: str, ip: str | None = None, host: str | None = None, online: bool = True
) -> TrackerInput:
    return TrackerInput(
        entity_id=f"device_tracker.t_{mac[-2:]}",
        platform="example_router",
        mac=mac,
        ip=ip,
        hostname=host,
        online=online,
    )


def test_confidence_levels_and_priority() -> None:
    """Each source gives its level; manual wins over everything."""
    snap = Snapshot(
        devices=[
            _device("reg_seen", macs=("00:00:5e:00:53:01",)),
            _device("reg_only", macs=("00:00:5e:00:53:02",)),
            _device("disc", config_entry_id="entry_disc"),
            _device("host", bt_macs=("00:00:5e:00:53:51",)),
            _device("byip", config_entry_id="entry_ip"),
            _device("manual_target"),
            _device("router_client", macs=("00:00:5e:00:53:09",), router_client=True),
        ],
        trackers=[
            _tracker("00:00:5e:00:53:01", "192.0.2.1"),
            _tracker("00:00:5e:00:53:03", "192.0.2.3"),
            _tracker("00:00:5e:00:53:50", "192.0.2.50", "ESP_005351"),
            _tracker("00:00:5e:00:53:04", "192.0.2.4"),
            _tracker("00:00:5e:00:53:01", "192.0.2.1", online=False),
            _tracker("00:00:5e:00:53:09", "192.0.2.9"),
            _tracker("00:00:5e:00:53:05", "192.0.2.5"),
        ],
        entries=[
            EntryInput("entry_disc", "example", unique_mac="00:00:5e:00:53:03"),
            EntryInput("entry_ip", "example", host_ip="192.0.2.4"),
            EntryInput("router_entry", "example_router", host_ip="192.0.2.5"),
        ],
        overrides={"00:00:5e:00:53:01": {"device_id": "manual_target", "label": None}},
        router_domains=frozenset({"example_router"}),
    )
    table = build_table(snap)
    conf = {row["mac"]: (row["confidence"], row["device_id"]) for row in table.rows}
    assert conf["00:00:5e:00:53:01"] == ("manual", "manual_target")
    assert conf["00:00:5e:00:53:02"] == ("registry", "reg_only")
    assert conf["00:00:5e:00:53:03"] == ("discovery", "disc")
    assert conf["00:00:5e:00:53:50"] == ("hostname", "host")
    assert conf["00:00:5e:00:53:04"] == ("ip", "byip")
    # A device created by the router for its tracker is not a match
    assert conf["00:00:5e:00:53:09"] == ("unmatched", None)
    assert table.by_mac["00:00:5e:00:53:09"]["router_device_id"] == "router_client"
    # The router's own config entry is not used for IP matching
    assert conf["00:00:5e:00:53:05"] == ("unmatched", None)
    # Bluetooth MAC keeps its own row
    assert table.by_mac["00:00:5e:00:53:51"]["mac_type"] == "bluetooth"
    # Rows are sorted by confidence
    assert table.rows[0]["confidence"] == "manual"
    assert table.rows[-1]["confidence"] == "unmatched"


def test_tracker_plus_registry_and_history() -> None:
    """A registry MAC seen by a router tracker (now or in history)."""
    snap = Snapshot(
        devices=[
            _device("a", macs=("00:00:5e:00:53:01",)),
            _device("b", macs=("00:00:5e:00:53:02",)),
        ],
        trackers=[_tracker("00:00:5e:00:53:01", "192.0.2.1", "Example-A")],
        history={
            "00:00:5e:00:53:02": {
                "first_seen": "2026-11-01T00:00:00+00:00",
                "last_seen": "2026-11-02T00:00:00+00:00",
                "hostname": "Example-B",
                "ips": ["192.0.2.2", "192.0.2.12"],
                "days": ["2026-11-01", "2026-11-02"],
            }
        },
    )
    table = build_table(snap)
    a = table.by_mac["00:00:5e:00:53:01"]
    b = table.by_mac["00:00:5e:00:53:02"]
    assert a["confidence"] == "tracker+registry"
    assert a["online"] is True
    assert b["confidence"] == "tracker+registry"
    assert b["online"] is False
    assert b["hostname"] == "Example-B"
    assert b["last_ips"] == ["192.0.2.2", "192.0.2.12"]
    assert table.by_previous_ip["192.0.2.12"] == ["00:00:5e:00:53:02"]


def test_ambiguous_candidates_are_not_matched() -> None:
    """A 3-byte suffix shared by two devices gives candidates, not a match."""
    snap = Snapshot(
        devices=[
            _device("one", macs=("00:00:5e:00:53:51",)),
            _device("two", bt_macs=("02:00:5e:00:53:51",)),
            _device("entry_a", config_entry_id="multi"),
            _device("entry_b", config_entry_id="multi"),
        ],
        trackers=[
            _tracker("00:00:5e:00:53:50", "192.0.2.50", "ESP_005351"),
            _tracker("00:00:5e:00:53:60", "192.0.2.60"),
        ],
        entries=[EntryInput("multi", "example", host_ip="192.0.2.60")],
    )
    table = build_table(snap)
    esp = table.by_mac["00:00:5e:00:53:50"]
    assert esp["confidence"] == "unmatched"
    assert {c["device_id"] for c in esp["candidates"]} == {"one", "two"}
    multi = table.by_mac["00:00:5e:00:53:60"]
    assert multi["confidence"] == "unmatched"
    assert {c["device_id"] for c in multi["candidates"]} == {"entry_a", "entry_b"}


def test_duplicates_label_and_random() -> None:
    """Duplicates are flagged, labels clear orphans, random MACs can be hidden."""
    devices = [
        _device("cloud", macs=("00:00:5e:00:53:70",), domain="tuya"),
        _device("local", macs=("00:00:5e:00:53:70",), domain="localtuya"),
    ]
    trackers = [
        _tracker("02:00:5e:00:53:40", "192.0.2.40"),
        _tracker("00:00:5e:00:53:41", "192.0.2.41"),
        _tracker("00:00:5e:00:53:42", "192.0.2.42"),
    ]
    overrides = {"00:00:5e:00:53:42": {"device_id": None, "label": "Guest laptop"}}
    table = build_table(Snapshot(devices=devices, trackers=trackers, overrides=overrides))
    dup = table.by_mac["00:00:5e:00:53:70"]
    assert dup["duplicate"] is True
    assert {c["integration"] for c in dup["candidates"]} == {"tuya", "localtuya"}
    assert table.duplicates[0]["mac"] == "00:00:5e:00:53:70"
    labelled = table.by_mac["00:00:5e:00:53:42"]
    assert labelled["confidence"] == "manual"
    assert labelled["label"] == "Guest laptop"
    summary = summarize(table)
    assert summary["orphans"] == 2  # random phone + laptop, not the labelled one
    assert summary["random"] == 1
    assert summary["duplicates"] == 1

    hidden = build_table(
        Snapshot(devices=devices, trackers=trackers, overrides=overrides, ignore_random=True)
    )
    assert "02:00:5e:00:53:40" not in hidden.by_mac


def test_dhcp_source_and_lookup() -> None:
    """DHCP adds IP/hostname for MACs the router does not track."""
    snap = Snapshot(
        devices=[
            _device("tv", macs=("00:00:5e:00:53:80",)),
            _device("fan", config_entry_id="e_fan"),
        ],
        dhcp=[DhcpInput("00:00:5e:00:53:80", "192.0.2.80", "example-tv")],
        entries=[EntryInput("e_fan", "example", host_ip="192.0.2.99", host_name=None)],
    )
    table = build_table(snap)
    row = table.by_mac["00:00:5e:00:53:80"]
    assert row["confidence"] == "registry"
    assert row["sources"] == ["registry", "dhcp"]
    assert row["ip"] == "192.0.2.80"

    by_host = lookup(table, "timeout talking to example-tv.local")
    assert [r["device_id"] for r in by_host["results"]] == ["tv"]
    assert by_host["results"][0]["matched_by"] == "hostname"
    assert by_host["extracted"]["hostnames"] == ["example-tv"]

    by_entry = lookup(table, "192.0.2.99 unreachable")
    assert by_entry["results"] == []
    assert by_entry["candidates"][0]["device_id"] == "fan"
    assert by_entry["candidates"][0]["matched_by"] == "config_entry_ip"

    unknown = lookup(table, "00:00:5e:00:53:ff and 192.0.2.250")
    assert unknown["unknown"] == ["00:00:5e:00:53:ff", "192.0.2.250"]
