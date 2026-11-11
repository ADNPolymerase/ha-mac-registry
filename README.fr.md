<p align="center">
  <img src="https://raw.githubusercontent.com/ADNPolymerase/ha-mac-registry/main/custom_components/mac_registry/brand/icon.png" alt="MAC Registry" width="128">
</p>

# MAC Registry

<a href="https://github.com/ADNPolymerase/ha-mac-registry/blob/main/README.md"><img src="https://raw.githubusercontent.com/ADNPolymerase/ha-mac-registry/main/docs/lang-en.svg" alt="Read in English" height="60"></a>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/ADNPolymerase/ha-mac-registry?sort=semver)](https://github.com/ADNPolymerase/ha-mac-registry/releases)
[![tests](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/tests.yml/badge.svg)](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/tests.yml)
[![hassfest](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/hassfest.yml/badge.svg)](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/hassfest.yml)
[![HACS Action](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/hacs.yml/badge.svg)](https://github.com/ADNPolymerase/ha-mac-registry/actions/workflows/hacs.yml)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/ADNPolymerase/ha-mac-registry/blob/main/LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg?logo=buy-me-a-coffee)](https://buymeacoffee.com/adnpolymerase)

<a href="https://buymeacoffee.com/adnpolymerase" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-orange.png" alt="Buy Me A Coffee" height="60"></a>
<a href="https://adnpolymerase.github.io/HA/" target="_blank"><img src="https://raw.githubusercontent.com/ADNPolymerase/HA/main/assets/site-button.svg" alt="Lien vers mon github.io pour mes autres projets" height="60"></a>


Les logs de Home Assistant ne parlent qu'en MAC, en IP ou en hostname. MAC Registry construit et tient à jour une table unique qui relie chaque MAC à son IP, son hostname, son `device_tracker` routeur, l'appareil Home Assistant (nom, intégration, fabricant, modèle) et ses entités. Tu colles une ligne de log, il te dit de quel appareil il s'agit.

- **Lecture seule** : rien n'est renommé, désactivé ni supprimé.
- **Aucun trafic réseau** : ni scan, ni ping. Il lit ce que Home Assistant sait déjà.
- **Persistant** : historique par MAC (première vue, dernière vue, 5 dernières IP), qui survit aux redémarrages et aux changements DHCP.

## Installation

Marketplace (Home Assistant 2026.11+), en dépôt personnalisé :

1. Le Marketplace a besoin d'une **connexion GitHub** pour ajouter un dépôt personnalisé.
2. **Paramètres → Marketplace**, menu **⋮**, **Dépôts personnalisés**.
3. Dépôt `https://github.com/ADNPolymerase/ha-mac-registry` (ou `ADNPolymerase/ha-mac-registry`), catégorie **Intégration**.
4. Installe **MAC Registry**, puis redémarre Home Assistant.
5. **Paramètres → Appareils et services → Ajouter une intégration → MAC Registry**.

Sous Home Assistant 2026.10, ajoute le même dépôt personnalisé dans HACS. Sans l'un ni l'autre, copie `custom_components/mac_registry` dans `/config/custom_components/` et redémarre.

Une seule instance est possible. Version minimale : Home Assistant 2026.10.

## Sources et niveaux de confiance

Chaque ligne indique ses `sources` (`registry`, `router`, `dhcp`, `history`, `manual`) et une `confidence`, par ordre de priorité :

| Niveau | Signification |
|---|---|
| `manual` | Association saisie dans les options (appareil ou libellé libre). L'emporte toujours. |
| `tracker+registry` | MAC présente dans `connections` d'un appareil **et** vue par un `device_tracker` routeur (maintenant ou dans l'historique). |
| `registry` | MAC présente dans `connections` d'un appareil, jamais vue par le routeur. |
| `discovery` | L'identifiant unique d'une config entry est cette MAC (cas typique des intégrations ajoutées par découverte DHCP/Zeroconf). |
| `hostname` | Le hostname désigne un appareil : MAC complète (`shelly1g3-00005e005301`), suffixe de 3 octets (`ESP_XXXXXX`, `tasmota-XXXXXX-NNNN`, `nom-XXXXXX`) ou `host` d'une config entry. |
| `ip` | L'IP actuelle correspond à la clé `host` / `ip_address` d'une config entry. |
| `unmatched` | Aucun appareil. Les candidats éventuels sont listés. |

Règles :

- Les MAC sont normalisées (`aa:bb:cc:dd:ee:ff`) quel que soit le format d'origine (`AA-BB-…`, sans séparateur, `aabb.ccdd.eeff`, `_`). Les adresses IEEE Zigbee (`0x…`) sont ignorées. Les MAC Bluetooth ont leur propre ligne (`mac_type: bluetooth`).
- Un rapprochement `hostname`, `ip` ou `discovery` n'est fait que si le **candidat est unique**. Sinon la ligne reste `unmatched` avec ses `candidates`.
- L'appareil que l'intégration routeur enregistre pour son propre tracker n'est pas un rapprochement à lui seul : c'est le côté routeur du lien, conservé dans `router_device_id`, et la ligne pointe vers l'appareil de l'intégration qui déclare la même MAC.
- Depuis Home Assistant 2026.10, plusieurs intégrations peuvent déclarer la même MAC, chacune sur son propre appareil. Ils sont reliés sur une seule ligne : l'appareil qui a le plus d'entités vient en tête et les autres figurent dans `linked_devices`. Seuls deux appareils de la **même** intégration qui partagent une MAC sont signalés (`duplicate: true` et une réparation).
- Config entries : seules les clés `host` et `ip_address` de `data` sont lues, ainsi que `unique_id`. Rien d'autre n'est lu, journalisé ou exposé (mots de passe compris).
- DHCP : lu via l'API publique `homeassistant.components.dhcp.async_discovered_service_info`. La source se désactive seule si elle n'est pas disponible. Zeroconf n'expose pas d'API publique équivalente et n'est pas utilisé.

## Propositions

Une MAC vue par le routeur mais reliée à aucun appareil reçoit jusqu'à trois appareils proposés dans sa liste `proposals` :

- les candidats ambigus trouvés par hostname ou IP ;
- les appareils dont le nom ressemble au nom du client dans le routeur (hostname, ou nom du tracker si tu l'as renommé). Les noms sont comparés mot à mot, sans accents, séparateurs ni mots creux comme `airplay`, et le fabricant compte : `Arlo-Avant` correspond à l'appareil `Avant` fabriqué par Arlo.

Seuls les appareils qui ne déclarent pas de MAC réseau sont proposés ; les autres sont déjà reliés par leur MAC. Rien n'est associé sans ton accord : accepte les propositions depuis la réparation ci-dessous, avec l'action `mac_registry.link`, ou dans les options.

## Capteur

`sensor.mac_registry_summary` : nombre de MAC connues. Les attributs donnent `matched`, `proposals` (orphelines avec un appareil proposé), `router_linked` (côté routeur relié à l'appareil d'une intégration), `orphans` (vues par le routeur sans appareil Home Assistant), `unseen_devices` (MAC d'appareils jamais vues par le routeur, Bluetooth et Zigbee exclus), `random`, `duplicates`, `by_confidence`, `dhcp_source`, ainsi que des listes courtes (`orphan_list`, `unseen_device_list`, `duplicate_list`). Les attributs restent sous 16 ko et les listes sont exclues du recorder.

## Actions

Dans **Outils de développement → Actions**, avec la réponse activée.

```yaml
action: mac_registry.lookup
data:
  query: "Cannot connect to host 192.0.2.207:80 ssl:default [Connect call failed ('192.0.2.207', 80)]"
```

La requête accepte une MAC, une IP, un hostname ou une ligne de log brute. La réponse contient :

- `extracted` : MAC, IP et hostnames reconnus dans la requête ;
- `results` : lignes complètes avec appareil, entités, tracker, `confidence` et `matched_by` (`mac`, `ip`, `previous_ip`, `hostname`, `mac_suffix`) ;
- `candidates` : appareils trouvés uniquement par leur config entry, ou correspondances ambiguës ;
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

```yaml
action: mac_registry.link
data:
  mac: "00:00:5e:00:53:41"
  device_id: 0123456789abcdef0123456789abcdef   # ou : label: "Portable bureau"
```

```yaml
action: mac_registry.unlink
data:
  mac: "00:00:5e:00:53:41"
```

`link` et `unlink` stockent l'association dans MAC Registry uniquement ; rien d'autre ne change dans Home Assistant.

## Événements

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

## WebSocket (administrateurs)

- `{"type": "mac_registry/list"}` renvoie `rows`, `summary` et `last_built`.
- `{"type": "mac_registry/lookup", "query": "..."}` renvoie la même réponse que l'action `lookup`.

## Options

- **Réglages** :
  - intervalle de secours (15 min par défaut ; la table se rafraîchit aussi sur les changements des registres et des trackers, avec un anti-rebond de 10 s) ;
  - intégrations routeur à utiliser (vide = tous les trackers `source_type: router`) ;
  - ignorer les MAC aléatoires (`x2`, `x6`, `xA`, `xE`) ;
  - utiliser le cache DHCP.
- **Ajouter une association manuelle** : MAC vers un appareil, ou libellé libre. Les associations sont stockées dans `.storage/mac_registry`, pas dans le navigateur.
- **Supprimer des associations manuelles**.

## Réparations

- Une seule issue groupée pour les MAC orphelines non aléatoires vues au moins 3 jours distincts. Ouvre-la pour la corriger en un seul écran : coche les propositions à accepter, et les MAC sans appareil Home Assistant pour leur donner leur nom comme libellé.
- Une issue groupée quand plusieurs appareils de la même intégration déclarent la même MAC.

Ces issues sont informatives : MAC Registry ne modifie rien.

## Diagnostics

Le téléchargement des diagnostics masque toutes les MAC, IP et hostnames.

## Bon à savoir

- Testé uniquement sur des **données simulées** (plages documentaires `192.0.2.x` et `00:00:5e:00:53:xx`), avec `pytest-homeassistant-custom-component` 0.13.375, soit Home Assistant 2026.10.4, la dernière version publiée au 10/11/2026. Le code a été relu contre les sources de Home Assistant 2026.11.1.
- La découverte Zeroconf n'est pas utilisée (pas d'API publique).
- Feuille de route : carte Lovelace servie par l'intégration (tableau filtrable, liens, bouton « associer »).

L'historique des versions est dans [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT
