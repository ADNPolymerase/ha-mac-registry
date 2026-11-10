# MAC Registry

[Français](#français) · [English](#english)

Home Assistant integration that maps **MAC ↔ IP ↔ hostname ↔ device_tracker ↔ device ↔ entities** and resolves a raw log line to the device behind it. Read-only, no network scanning.

---

## Français

Les logs de Home Assistant ne parlent qu'en MAC, en IP ou en hostname. MAC Registry construit et tient à jour une table unique qui relie chaque MAC à son IP, son hostname, son `device_tracker` routeur, l'appareil HA (nom, intégration, fabricant, modèle) et ses entités. Tu colles une ligne de log, il te dit de quel appareil il s'agit.

- **Lecture seule** : rien n'est renommé, désactivé ni supprimé.
- **Aucun trafic réseau** : ni scan, ni ping. Il lit ce que HA sait déjà.
- **Persistant** : historique par MAC (première vue, dernière vue, 5 dernières IP), qui survit aux redémarrages et aux changements DHCP.

### Installation (Marketplace, dépôt personnalisé)

1. Le Marketplace a besoin d'une **connexion GitHub** pour ajouter un dépôt personnalisé.
2. **Paramètres → Marketplace**, menu **⋮**, **Dépôts personnalisés**.
3. Dépôt : `https://github.com/ADNPolymerase/ha-mac-registry` (ou `ADNPolymerase/ha-mac-registry`), catégorie **Intégration**.
4. Installe **MAC Registry**, puis redémarre Home Assistant.
5. **Paramètres → Appareils et services → Ajouter une intégration → MAC Registry**.

Une seule instance est possible. Version minimale : Home Assistant 2026.10.

### Sources et niveaux de confiance

Chaque ligne indique ses `sources` (`registry`, `router`, `dhcp`, `history`, `manual`) et une `confidence`, par ordre de priorité :

| Niveau | Signification |
|---|---|
| `manual` | Association saisie dans les options (appareil ou libellé libre). L'emporte toujours. |
| `tracker+registry` | MAC présente dans `connections` d'un appareil HA **et** vue par un `device_tracker` routeur (maintenant ou dans l'historique). |
| `registry` | MAC présente dans `connections` d'un appareil HA, jamais vue par le routeur. |
| `discovery` | L'identifiant unique d'une config entry est cette MAC (cas typique des intégrations ajoutées par découverte DHCP/Zeroconf). |
| `hostname` | Le hostname désigne un appareil : MAC complète (`shelly1g3-00005e005301`), suffixe de 3 octets (`ESP_XXXXXX`, `tasmota-XXXXXX-NNNN`, `nom-XXXXXX`) ou `host` d'une config entry. |
| `ip` | L'IP actuelle correspond à la clé `host` / `ip_address` d'une config entry. |
| `unmatched` | Aucun appareil. Les candidats éventuels sont listés. |

Règles :
- Les MAC sont normalisées (`aa:bb:cc:dd:ee:ff`) quel que soit le format d'origine (`AA-BB-…`, sans séparateur, `aabb.ccdd.eeff`, `_`). Les adresses IEEE Zigbee (`0x…`) sont ignorées. Les MAC Bluetooth ont leur propre ligne (`mac_type: bluetooth`).
- Un rapprochement `hostname`, `ip` ou `discovery` n'est fait que si le **candidat est unique**. Sinon la ligne reste `unmatched` avec ses `candidates`.
- L'appareil que l'intégration routeur crée pour son tracker ne compte pas comme rapproché (il figure dans `router_device_id`).
- Depuis HA 2026.10, deux intégrations peuvent déclarer la même MAC sur deux appareils distincts. MAC Registry le signale (`duplicate: true` et une réparation).
- Config entries : seules les clés `host` et `ip_address` de `data` sont lues, ainsi que `unique_id`. Rien d'autre n'est lu, journalisé ou exposé (mots de passe compris).
- DHCP : lu via l'API publique `homeassistant.components.dhcp.async_discovered_service_info`. La source se désactive seule si elle n'est pas disponible. Zeroconf n'expose pas d'API publique équivalente et n'est pas utilisé.

### Capteur

`sensor.mac_registry_summary` : nombre de MAC connues. Les attributs donnent `matched`, `orphans` (vues par le routeur sans appareil HA), `unseen_devices` (MAC d'appareils HA jamais vues par le routeur, Bluetooth et Zigbee exclus), `random`, `duplicates`, `by_confidence`, `dhcp_source`, ainsi que des listes courtes (`orphan_list`, `unseen_device_list`, `duplicate_list`). Les attributs restent sous 16 ko et les listes sont exclues du recorder.

### Actions (services)

Dans **Outils de développement → Actions**, avec « Réponse » activé.

```yaml
action: mac_registry.lookup
data:
  query: "Cannot connect to host 192.0.2.207:80 ssl:default [Connect call failed ('192.0.2.207', 80)]"
```

La requête accepte une MAC, une IP, un hostname ou une ligne de log brute. La réponse contient :
- `extracted` : MAC, IP et hostnames reconnus dans la requête ;
- `results` : lignes complètes avec appareil, entités, tracker, `confidence`, `matched_by` (`mac`, `ip`, `previous_ip`, `hostname`, `mac_suffix`) ;
- `candidates` : appareils trouvés uniquement par la config entry, ou correspondances ambiguës ;
- `unknown` : identifiants jamais vus.

```yaml
action: mac_registry.lookup
data:
  query: "RPC call error ... shelly1g3-00005e005301"
```

```yaml
action: mac_registry.export
data:
  path: mac_registry.md        # facultatif ; .md ou .csv ; relatif à /config
```

`export` renvoie toujours toute la table en JSON. Avec `path`, il écrit aussi un fichier Markdown ou CSV. Le chemin doit rester dans `/config` : pas de `..`, pas de lien symbolique vers l'extérieur, pas de dossier caché (`.storage`), et le dossier doit exister.

```yaml
action: mac_registry.refresh
```

### Événements

- `mac_registry_new_mac` : MAC jamais vue (aucun événement au tout premier passage, pour éviter une rafale à l'installation).
- `mac_registry_ip_changed` : `old_ip` / `new_ip` d'une MAC connue.

Les données contiennent `mac`, `ip`, `hostname`, `tracker`, `device_id`, `device_name`, `integration`, `confidence`, `random`.

```yaml
triggers:
  - trigger: event
    event_type: mac_registry_new_mac
actions:
  - action: persistent_notification.create
    data:
      title: Nouvel appareil sur le réseau
      message: "{{ trigger.event.data.mac }} ({{ trigger.event.data.hostname }}, {{ trigger.event.data.ip }})"
```

### WebSocket (administrateurs)

- `{"type": "mac_registry/list"}` renvoie `rows`, `summary` et `last_built`.
- `{"type": "mac_registry/lookup", "query": "..."}` renvoie la même réponse que l'action `lookup`.

### Options

- **Réglages** :
  - intervalle de secours (15 min par défaut ; la table se rafraîchit aussi sur les changements des registres et des trackers, avec un anti-rebond de 10 s) ;
  - intégrations routeur à utiliser (vide = tous les trackers `source_type: router`) ;
  - ignorer les MAC aléatoires (`x2`, `x6`, `xA`, `xE`) ;
  - utiliser le cache DHCP.
- **Ajouter une association manuelle** : MAC vers un appareil, ou libellé libre. Les associations sont stockées dans `.storage/mac_registry`, pas dans le navigateur.
- **Supprimer des associations manuelles**.

### Réparations

- Une seule issue groupée pour les MAC orphelines non aléatoires vues au moins 3 jours distincts. Un libellé manuel retire la MAC de la liste.
- Une issue groupée quand plusieurs appareils HA déclarent la même MAC.

Ces issues sont informatives : MAC Registry ne modifie rien.

### Diagnostics

Le téléchargement des diagnostics masque toutes les MAC, IP et hostnames.

### Limites connues

- Testé uniquement sur des **données simulées** (plages documentaires `192.0.2.x` et `00:00:5e:00:53:xx`), avec `pytest-homeassistant-custom-component` 0.13.375, soit HA 2026.10.4, la dernière version publiée au 10/11/2026. Le code a été relu contre les sources de HA 2026.11.1.
- La découverte Zeroconf n'est pas utilisée (pas d'API publique).
- Feuille de route : carte Lovelace servie par l'intégration (tableau filtrable, liens, bouton « associer »), prévue en phase 2.

---

## English

Home Assistant logs only speak MAC, IP or hostname. MAC Registry builds and maintains one table linking every MAC to its IP, hostname, router `device_tracker`, Home Assistant device (name, integration, manufacturer, model) and entities. Paste a log line and it tells you which device it is.

- **Read-only**: nothing is renamed, disabled or removed.
- **No network traffic**: no scan, no ping. It only reads what Home Assistant already knows.
- **Persistent**: per-MAC history (first seen, last seen, last 5 IPs) that survives restarts and DHCP changes.

### Installation (Marketplace, custom repository)

1. Adding a custom repository requires a **GitHub connection** in the Marketplace.
2. **Settings → Marketplace**, **⋮** menu, **Custom repositories**.
3. Repository `https://github.com/ADNPolymerase/ha-mac-registry` (or `ADNPolymerase/ha-mac-registry`), category **Integration**.
4. Install **MAC Registry** and restart Home Assistant.
5. **Settings → Devices & services → Add integration → MAC Registry**.

Single instance. Minimum version: Home Assistant 2026.10.

### Sources and confidence

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
- MACs are normalised to `aa:bb:cc:dd:ee:ff` whatever the input format. Zigbee IEEE addresses are ignored. Bluetooth MACs get their own row.
- `hostname`, `ip` and `discovery` links require a **single candidate**. Otherwise the row stays `unmatched` with its `candidates`.
- The device a router integration creates for its tracker does not count as a match (it is kept in `router_device_id`).
- Since HA 2026.10, two integrations can declare the same MAC on two separate devices. MAC Registry flags it (`duplicate: true` and a repair).
- From config entries only `data["host"]`, `data["ip_address"]` and `unique_id` are read. Nothing else is read, logged or exposed (passwords included).
- DHCP is read through the public `homeassistant.components.dhcp.async_discovered_service_info` helper and disabled automatically when unavailable. Zeroconf has no equivalent public API and is not used.

### Sensor

`sensor.mac_registry_summary` holds the number of known MACs. Its attributes give `matched`, `orphans` (seen by the router, no HA device), `unseen_devices` (HA device MACs never seen by the router, Bluetooth and Zigbee excluded), `random`, `duplicates`, `by_confidence`, `dhcp_source`, plus short lists. Attributes stay below 16 kB and the lists are excluded from the recorder.

### Actions

```yaml
action: mac_registry.lookup
data:
  query: "Roomba 192.0.2.198 connection lost ([Errno 113] Host is unreachable)"
```

```yaml
action: mac_registry.export
data:
  path: mac_registry.csv   # optional, .csv or .md, relative to /config
```

```yaml
action: mac_registry.refresh
```

`lookup` accepts a MAC, an IP, a hostname or a raw log line and returns `extracted`, `results` (full rows with `matched_by`), `candidates` and `unknown`. `export` always returns the whole table as JSON. With `path` it also writes a file, which must stay inside `/config`: no `..`, no symlink pointing outside, no hidden folder, and the folder must exist.

### Events

- `mac_registry_new_mac`: a MAC never seen before (none on the very first pass).
- `mac_registry_ip_changed`: `old_ip` / `new_ip` of a known MAC.

### WebSocket (admin only)

- `mac_registry/list`
- `mac_registry/lookup` with a `query` field.

### Options

- **Settings**: safety refresh interval (default 15 min, plus debounced event-driven refreshes), router integrations to use, ignore random MACs, use the DHCP cache.
- **Manual links**: MAC to a device or a free label, stored in `.storage/mac_registry`.

### Repairs

- One grouped issue for non-random orphan MACs seen on at least 3 different days. A label removes a MAC from it.
- One grouped issue for MACs declared by several devices.

### Diagnostics

MACs, IPs and hostnames are masked.

### Known limits

- Tested on **simulated data only** (documentation ranges), with `pytest-homeassistant-custom-component` 0.13.375 (HA 2026.10.4, the latest release on 2026-11-10). The code was reviewed against the HA 2026.11.1 sources.
- Zeroconf is not used.
- Roadmap: a Lovelace card served by the integration (phase 2).

## License

MIT
