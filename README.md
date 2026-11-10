<p align="center">
  <img src="https://raw.githubusercontent.com/ADNPolymerase/ha-mac-registry/main/custom_components/mac_registry/brand/icon.png" alt="MAC Registry" width="128">
</p>

# MAC Registry

<a href="https://github.com/ADNPolymerase/ha-mac-registry/blob/main/README.fr.md"><img src="https://raw.githubusercontent.com/ADNPolymerase/ha-mac-registry/main/docs/lang-fr.svg" alt="Lire en français" height="60"></a>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/ADNPolymerase/ha-mac-registry?sort=semver)](https://github.com/ADNPolymerase/ha-mac-registry/releases)
[![tests](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/tests.yml/badge.svg)](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/tests.yml)
[![hassfest](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/hassfest.yml/badge.svg)](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/hassfest.yml)
[![HACS Action](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/hacs.yml/badge.svg)](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/hacs.yml)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/ADNPolymerase/ha-mac-registry/blob/main/LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg?logo=buy-me-a-coffee)](https://buymeacoffee.com/adnpolymerase)

<a href="https://buymeacoffee.com/adnpolymerase" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-orange.png" alt="Buy Me A Coffee" height="60"></a>
<a href="https://adnpolymerase.github.io/HA/" target="_blank"><img src="https://raw.githubusercontent.com/ADNPolymerase/HA/main/assets/site-button.svg" alt="Link to my github.io for my other projects" height="60"></a>


Home Assistant logs only speak MAC, IP or hostname. MAC Registry builds and maintains one table linking every MAC to its IP, hostname, router `device_tracker`, Home Assistant device (name, integration, manufacturer, model) and entities. Paste a log line and it tells you which device it is.

- **Read-only**: nothing is renamed, disabled or removed.
- **No network traffic**: no scan, no ping. It only reads what Home Assistant already knows.
- **Persistent**: per-MAC history (first seen, last seen, last 5 IPs) that survives restarts and DHCP changes.

## Installation

Marketplace (Home Assistant 2026.11+), as a custom repository:

1. Adding a custom repository requires a **GitHub connection** in the Marketplace.
2. **Settings → Marketplace**, **⋮** menu, **Custom repositories**.
3. Repository `https://github.com/ADNPolymerase/ha-mac-registry` (or `ADNPolymerase/ha-mac-registry`), category **Integration**.
4. Install **MAC Registry** and restart Home Assistant.
5. **Settings → Devices & services → Add integration → MAC Registry**.

On Home Assistant 2026.10, add the same custom repository in HACS. Without either, copy `custom_components/mac_registry` into `/config/custom_components/` and restart.

Single instance. Minimum version: Home Assistant 2026.10.

## Sources and confidence

Each row lists its `sources` (`registry`, `router`, `dhcp`, `history`, `manual`) and a `confidence`, highest priority first:

| Level | Meaning |
|---|---|
| `manual` | Link entered in the options (device or free label). Always wins. |
| `tracker+registry` | MAC in a device's `connections` **and** seen by a router `device_tracker` (now or in the history). |
| `registry` | MAC in a device's `connections`, never seen by the router. |
| `discovery` | A config entry's unique ID is this MAC (typical of entries created by DHCP/Zeroconf discovery). |
| `hostname` | The hostname points to a device: full MAC (`shelly1g3-00005e005301`), 3-byte suffix (`ESP_XXXXXX`, `tasmota-XXXXXX-NNNN`, `name-XXXXXX`) or a config entry `host`. |
| `ip` | The current IP equals a config entry's `host` / `ip_address`. |
| `unmatched` | No device. Candidates, if any, are listed. |

Rules:

- MACs are normalised to `aa:bb:cc:dd:ee:ff` whatever the input format (`AA-BB-…`, no separator, `aabb.ccdd.eeff`, `_`). Zigbee IEEE addresses (`0x…`) are ignored. Bluetooth MACs get their own row (`mac_type: bluetooth`).
- `hostname`, `ip` and `discovery` links require a **single candidate**. Otherwise the row stays `unmatched` with its `candidates`.
- The device a router integration registers for its own tracker is not a match by itself: it is the router side of the link, kept in `router_device_id`, and the row points to the device of the integration that declares the same MAC.
- Since Home Assistant 2026.10, several integrations can declare the same MAC, each on its own device. They are linked on one row: the device with the most entities leads and the others are listed in `linked_devices`. Only two devices of the **same** integration sharing a MAC are flagged (`duplicate: true` and a repair).
- From config entries only `data["host"]`, `data["ip_address"]` and `unique_id` are read. Nothing else is read, logged or exposed (passwords included).
- DHCP is read through the public `homeassistant.components.dhcp.async_discovered_service_info` helper and disabled automatically when unavailable. Zeroconf has no equivalent public API and is not used.

## Sensor

`sensor.mac_registry_summary` holds the number of known MACs. Its attributes give `matched`, `router_linked` (router side linked to an integration's device), `orphans` (seen by the router, no Home Assistant device), `unseen_devices` (device MACs never seen by the router, Bluetooth and Zigbee excluded), `random`, `duplicates`, `by_confidence`, `dhcp_source`, plus short lists (`orphan_list`, `unseen_device_list`, `duplicate_list`). Attributes stay below 16 kB and the lists are excluded from the recorder.

## Actions

In **Developer tools → Actions**, with the response enabled.

```yaml
action: mac_registry.lookup
data:
  query: "Cannot connect to host 192.0.2.207:80 ssl:default [Connect call failed ('192.0.2.207', 80)]"
```

The query accepts a MAC, an IP, a hostname or a raw log line. The response holds:

- `extracted`: MACs, IPs and hostnames recognised in the query;
- `results`: full rows with device, entities, tracker, `confidence` and `matched_by` (`mac`, `ip`, `previous_ip`, `hostname`, `mac_suffix`);
- `candidates`: devices found only through their config entry, or ambiguous matches;
- `unknown`: identifiers never seen.

```yaml
action: mac_registry.lookup
data:
  query: "RPC call error ... shelly1g3-00005e005301"
```

```yaml
action: mac_registry.export
data:
  path: mac_registry.md        # optional; .md or .csv; relative to /config
```

`export` always returns the whole table as JSON. With `path` it also writes a Markdown or CSV file, which must stay inside `/config`: no `..`, no symlink pointing outside, no hidden folder (`.storage`), and the folder must exist.

```yaml
action: mac_registry.refresh
```

## Events

- `mac_registry_new_mac`: a MAC never seen before (none on the very first pass, to avoid a burst at installation).
- `mac_registry_ip_changed`: `old_ip` / `new_ip` of a known MAC.

Event data holds `mac`, `ip`, `hostname`, `tracker`, `device_id`, `device_name`, `integration`, `confidence`, `random`.

```yaml
triggers:
  - trigger: event
    event_type: mac_registry_new_mac
actions:
  - action: persistent_notification.create
    data:
      title: New device on the network
      message: "{{ trigger.event.data.mac }} ({{ trigger.event.data.hostname }}, {{ trigger.event.data.ip }})"
```

## WebSocket (admin only)

- `{"type": "mac_registry/list"}` returns `rows`, `summary` and `last_built`.
- `{"type": "mac_registry/lookup", "query": "..."}` returns the same response as the `lookup` action.

## Options

- **Settings**:
  - safety refresh interval (15 min by default; the table also refreshes on registry and tracker changes, with a 10 s debounce);
  - router integrations to use (empty = every `source_type: router` tracker);
  - ignore random MACs (`x2`, `x6`, `xA`, `xE`);
  - use the DHCP cache.
- **Add a manual link**: MAC to a device, or a free label. Links are stored in `.storage/mac_registry`, not in the browser.
- **Remove manual links**.

## Repairs

- One grouped issue for non-random orphan MACs seen on at least 3 different days. A label removes a MAC from it.
- One grouped issue when several devices of the same integration declare the same MAC.

These issues are informative: MAC Registry changes nothing.

## Diagnostics

Downloaded diagnostics mask every MAC, IP and hostname.

## Good to know

- Tested on **simulated data only** (documentation ranges `192.0.2.x` and `00:00:5e:00:53:xx`), with `pytest-homeassistant-custom-component` 0.13.375, i.e. Home Assistant 2026.10.4, the latest release on 2026-11-10. The code was reviewed against the Home Assistant 2026.11.1 sources.
- Zeroconf discovery is not used (no public API).
- Roadmap: a Lovelace card served by the integration (filterable table, links, "link" button).

See [CHANGELOG.md](CHANGELOG.md) for the release history.

## License

MIT
