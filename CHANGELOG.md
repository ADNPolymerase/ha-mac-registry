# Changelog

## 0.3.0 - 2026-11-21

### Added

- **Optional: write an accepted link's MAC to its device.** Off by default. With **Write accepted links to the device** on in the options, accepting a link (repair, options, `mac_registry.link`) also adds the MAC to the device in the device registry, so the device can be found by MAC, for instance by a router card that links each client to its device. `mac_registry.link` gains `write_to_registry` to ask for it once.
  - Only a MAC the device does not declare yet is added, and each addition is recorded in `.storage/mac_registry`. Only recorded additions are removed: when the link is removed or moved, with the new `mac_registry.revert_written` action, or automatically when MAC Registry is removed.
  - Refused, nothing changes: Bluetooth MACs, service devices, devices of a router integration, and a MAC another device of the same integration declares. The action reports why; the repair and the options keep the link and log the refusal.
  - A written MAC that the device's integration later drops is counted in `written_lost` and not written again.
- `sensor.mac_registry_summary` gains `written` and `written_lost`; diagnostics show the option and the counts.

### Changed

- **MAC Registry is read-only by default**, no longer read-only in every case. Nothing is renamed, disabled or removed, and devices are only changed through the option above.

## 0.2.2 - 2026-11-21

### Fixed

- **Fewer wrong proposals.** On a real network two thirds of the name proposals pointed to the wrong device. Proposals now:
  - skip service devices (an integration such as Spook or Uptime Kuma, a Hue zone, a Marketplace repository), which were proposed for `HomeAssistant-Pi`, `HomePod-Kitchen` or `Netatmo`;
  - skip the parts of a device that is already linked by its MAC, such as the outlets `Switch 1` and `Switch 2` of a power strip;
  - need a word that tells devices apart. Product, brand and kind words alone (`iPhone`, `MacBookPro`, `Switch`), even in equal names, only propose a device when it is the only one that matches and no other client goes by the same name: two iPhones are no longer proposed for a client named `iPhone`, and four clients called `MacBookPro`, which may be several laptops, get no guess;
  - compare one name inside another on whole words only: `Switch` no longer matches a `SwitchBot` air conditioner;
  - score lower when the client name states a kind or brand the device does not have (`iPhone-Alice` and a bedside clock named `Alice`, `Meross-Smart-Switch` and a Nintendo `Switch`);
  - drop the devices that score clearly below the best one (a `Kitchen` lamp next to `HomePod Kitchen`).

### Changed

- **A phone gets the same proposal for each of its MACs.** Wi-Fi band suffixes (`-2G`, `-5G`, `-6G`, `2.4GHz`) are ignored, so `iPhone-Alice-6G` matches like `iPhone-Alice`.
- **A renamed device is also compared by its original name**, so a camera renamed `Living room camera` is still proposed for the client `Example-Welcome-0a1b2c`.

## 0.2.1 - 2026-11-20

### Fixed

- **tplink_router 2.47+: every client was taken for a real device.** That version gives each client tracker a device of its own, described by the client's MAC, and may add a "Blocked" switch to it, disabled by default. MAC Registry recognised a router-side device only when it held nothing but trackers, so these devices counted as matches: orphan MACs looked linked to their own router device, and their proposals and repair disappeared. A device of a router integration is now on the router side when it carries no identifier of that integration; the router itself and its mesh nodes, which do, stay real devices.

## 0.2.0 - 2026-11-11

### Added

- **Proposals for orphan MACs.** A MAC seen by the router but linked to no device gets up to three proposed devices: the ambiguous candidates found by hostname or IP, then the devices whose name looks like the client's name in the router (its hostname, or the tracker's name when you renamed it). Names are compared word by word, without accents, separators or filler words such as `airplay`, and the device's manufacturer counts (`Arlo-Avant` matches the device `Avant` made by Arlo). Only devices that declare no network MAC are proposed: the others are already linked by their MAC. Each row has a `proposals` list with the device, the reason and a score; nothing is linked without your approval.
- **The orphan repair can be fixed in one screen.** Open it from Settings → Repairs: tick the proposals to accept, and the MACs that have no Home Assistant device (phone, computer, guest) to label them with their name. Nothing is selected by default.
- **`mac_registry.link` and `mac_registry.unlink` actions** to link a MAC to a device or give it a label from an automation or a script, and to remove that link.
- `sensor.mac_registry_summary` gains `proposals` and a short `proposal_list`. The CSV export gains a `proposals` column.

### Good to know

- Accepting a proposal or labelling a MAC is stored in MAC Registry only (`.storage/mac_registry`), like the manual links of the options. Nothing else in Home Assistant is changed.

## 0.1.1 - 2026-11-10

### Fixed

- **A router client and the integration's device sharing its MAC are now linked instead of reported as duplicates.** Since Home Assistant 2026.10 a router integration registers its own device, with the same MAC, for each client another integration knows. That device was only recognised while its tracker had a live router state, so a restored, unavailable or removed tracker raised the "MAC declared by several devices" repair for a normal pair. It is now recognised by what it is: a device of a router integration that carries nothing but device trackers. The row is `tracker+registry`, keeps the router device in `router_device_id` and shows its tracker even without a state.

### Changed

- **Devices of different integrations sharing a MAC are linked, not flagged.** The device with the most entities leads the row and the others are listed in `linked_devices`, with their entities. The duplicate repair is now only raised when two devices of the same integration declare the same MAC.
- `sensor.mac_registry_summary` gains `router_linked`: the MACs for which the router side and an integration's device are linked. The CSV export gains a `linked_devices` column.

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
