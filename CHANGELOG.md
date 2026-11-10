# Changelog

## 0.1.0 - 2026-11-10

First release.

### Added

- **One table linking MAC, IP, hostname, router `device_tracker`, device and entities.** Each row gives its sources and a confidence level: `manual`, `tracker+registry`, `registry`, `discovery`, `hostname`, `ip` or `unmatched`. A hostname, IP or discovery link is only made when a single device matches; otherwise the candidates are listed.
- **`mac_registry.lookup` action.** Takes a MAC, an IP, a hostname or a raw log line and returns the device, its entities, its tracker and how it was matched. An IP the device no longer has still resolves through the history.
- **`mac_registry.export` action.** Returns the whole table as JSON and can write it as Markdown or CSV inside the configuration folder.
- **`mac_registry.refresh` action**, and the `mac_registry/list` and `mac_registry/lookup` WebSocket commands for administrators.
- **`sensor.mac_registry_summary`.** Known, matched and orphan MACs, devices never seen by the router, random and duplicated MACs, with short lists kept out of the recorder.
- **`mac_registry_new_mac` and `mac_registry_ip_changed` events.** Nothing is fired on the very first pass.
- **History per MAC** (first seen, last seen, last 5 IPs) stored in `.storage`, which survives restarts and DHCP changes.
- **Manual links** in the options: a MAC to a device, or a free label. Other options: refresh interval, router integrations to use, ignore random MACs, use the DHCP cache.
- **Repairs.** One grouped issue for non-random orphan MACs seen on at least 3 different days, one for MACs declared by several devices.
- **Diagnostics** with every MAC, IP and hostname masked.
- English and French translations.

### Good to know

- Read-only: nothing is renamed, disabled or removed, and no network traffic is sent.
- From config entries, only `host`, `ip_address` and the unique ID are read.
- Tested on simulated data only, with Home Assistant 2026.10.4, and reviewed against the Home Assistant 2026.11.1 sources.
