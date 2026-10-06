# Services externes d'ACCIMAP

Inventaire établi à partir du **code** (et non de la documentation), puis vérifié en conditions réelles le
6 octobre 2026 (phase 9) avec `scripts/check_external_services.py`.

Aucune autre ressource externe n'est chargée : Leaflet, MarkerCluster, Leaflet.heat, Chart.js, Bootstrap et Lucide
sont **embarqués** dans `static/vendor/` (aucun CDN, aucune police externe).

| Service | Appelé par | Obligatoire | Coût | Clé / compte | État vérifié |
|---|---|---|---|---|---|
| Tuiles OpenStreetMap | navigateur | oui (fond de carte) | gratuit, usage modéré | non | **fonctionnel** |
| Nominatim (géocodage inverse) | serveur ACCIMAP | non (facultatif) | gratuit, usage modéré | non (contact recommandé) | **fonctionnel**, `GEOCODER_CONTACT` à renseigner |
| Brevo (SMS OTP) | serveur ACCIMAP | oui en production réelle | payant (crédits SMS) | oui (`SMS_API_KEY`) | **intégré, non utilisé** : prototype en SMS simulé, aucun SMS réel envoyé |
| Stockage des photos | serveur ACCIMAP | oui | — | non | disque local (pas de service externe) |
| Render (hébergement + PostgreSQL/PostGIS) | — | pour le déploiement | offre gratuite limitée / payant | compte | **phase 10**, non configuré |

---

## 1. Tuiles OpenStreetMap

| | |
|---|---|
| Rôle | Fond de carte : carte publique, carte administrative, étape « Lieu » du signalement, mini-carte de la fiche |
| URL | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` (variable `MAP_TILE_URL`) |
| Méthode | `GET` d'images PNG 256×256, par le **navigateur** du visiteur (Leaflet) |
| Paramètres | `z` (zoom 0–19), `x`, `y` (tuile). Le navigateur envoie le `Referer` de la page ACCIMAP |
| Clé | aucune |
| Attribution | « © Contributeurs OpenStreetMap », affichée en bas à droite de chaque carte (obligatoire) |
| Politique | https://operations.osmfoundation.org/policies/tiles/ : usage modéré, pas de téléchargement massif, pas de garantie de service |

**Résultat attendu** : tuiles chargées (HTTP 200, `image/png`) au chargement, au zoom et au déplacement ;
avertissement lisible si les tuiles sont inaccessibles, sans bloquer la carte ni le signalement.

**Résultat obtenu (réel)**

| Vérification | Résultat |
|---|---|
| Carte publique, ordinateur : chargement initial | 18 tuiles, toutes HTTP 200 `image/png` |
| Zoom avant ×2 (bouton +) | tuiles des niveaux 13 et 14 chargées |
| Déplacement (glisser) | 7 à 8 nouvelles tuiles chargées |
| Zoom arrière | tuiles chargées ; 71 requêtes au total, **0 erreur** |
| Carte publique, smartphone | 29 à 30 requêtes, **0 erreur** |
| Étape « Lieu » du signalement | tuiles du niveau 17 chargées ; quelques tuiles du niveau 12 **annulées par Leaflet** (`net::ERR_ABORTED`) quand la carte saute sur la position GPS : comportement normal, pas une erreur |
| Attribution visible | oui |
| Coupure des tuiles (simulée côté navigateur) | carte publique : « Le fond de carte n'a pas pu être chargé… Les signalements restent affichés. » ; signalement : position GPS conservée, confirmation possible |

Captures (dossier `e2e_screenshots/`, non versionné) : `desktop_osm_1_initial.png`, `desktop_osm_2_after_zoom_pan.png`,
`mobile_osm_*.png`, `osm_3_tiles_down_public.png`, `osm_4_tiles_down_report.png`, `osm_5_report_location_real.png`.

**Limitations relevées**
- Le serveur de tuiles public d'OSM convient à un prototype, pas à un trafic important : au-delà, utiliser un
  fournisseur dédié en changeant uniquement `MAP_TILE_URL` (et l'attribution correspondante).
- Étape « Lieu » : l'avertissement « fond de carte » et le message GPS partagent la même zone d'affichage
  (`#location-status`). Selon l'ordre d'arrivée, « Position trouvée » remplace l'avertissement (observé 3 fois sur 5).
  Sans conséquence fonctionnelle (position et confirmation fonctionnent), mais l'utilisateur peut ne pas savoir
  pourquoi la carte est grise. Correction possible : zone d'avertissement distincte.
- Mini-carte de la fiche administrateur (`detail_map.js`) : aucun avertissement si les tuiles échouent (la carte
  reste grise avec son repère).

**Tester** : `python scripts/check_external_services.py` (avec `SKIP_NOMINATIM=1` pour les tuiles seules).

---

## 2. Nominatim (géocodage inverse)

| | |
|---|---|
| Rôle | Afficher « Quartier, Ville » à l'étape « Lieu » du signalement (information facultative) |
| URL | `https://nominatim.openstreetmap.org/reverse` (variable `GEOCODER_URL`) |
| Appelant | le **serveur** ACCIMAP (`reports/geocoding.py`), via `GET /report/place/?latitude=…&longitude=…` ; le navigateur ne contacte jamais Nominatim (l'IP du citoyen n'est pas transmise) |
| Méthode | `GET` |
| Paramètres | `format=jsonv2`, `lat`, `lon` (6 décimales), `zoom=16`, `addressdetails=1`, `accept-language=fr` |
| En-têtes | `User-Agent: ACCIMAP-prototype/1.0 (<GEOCODER_CONTACT>)`, `Accept: application/json` |
| Clé | **aucune** (le service n'en utilise pas) |
| Protections côté ACCIMAP | délai maximal 3 s ; au plus 1 appel/s par processus ; cache 24 h par position (~11 m), 60 s en cas d'échec ; désactivable (`REVERSE_GEOCODING_ENABLED=False`) ; jamais bloquant |
| Politique | https://operations.osmfoundation.org/policies/nominatim/ : 1 requête/s maximum, application identifiée, pas d'usage massif |

**Résultat attendu** : HTTP 200 avec une adresse au Togo ; libellé court « Quartier, Ville » ; en cas d'erreur
ou de service lent : aucun libellé, aucun blocage, aucune exception.

**Résultat obtenu (réel, 3 appels au total)**

| Vérification | Résultat |
|---|---|
| `lat=6.138500&lon=1.226500` | HTTP 200 en 0,61 s ; « Rue de Paris, Doulassamé, 3e Arrondissement, Lomé, Région Maritime, Togo » ; `country_code=tg` |
| Libellé construit | **« Doulassamé, Lomé »** |
| `reverse_geocode(6.1720, 1.2140)` (chemin applicatif réel) | « Université du Bénin, Lomé » |
| Même position redemandée | servie par le cache, sans appel réseau |
| 3e appel immédiat, autre position | non transmis (limitation 1 appel/s), retourne `None` |
| Service injoignable (adresse locale fermée) | `None`, sans exception, en ~2 s |
| `REVERSE_GEOCODING_ENABLED=False` | aucun appel |
| Navigateur, étape « Lieu » (via `/report/place/`) | « Doulassamé, Lomé » affiché |
| `GEOCODER_CONTACT` | **vide** : à renseigner |

**Limitations**
- `GEOCODER_CONTACT` est vide : la politique demande d'identifier l'application ; le `User-Agent` l'identifie déjà,
  mais un contact permet à OSM de joindre l'exploitant avant un éventuel blocage. À renseigner dans `.env` et sur
  Render, par exemple `GEOCODER_CONTACT=contact@votre-domaine.tg` (adresse de l'équipe/projet, pas une adresse
  personnelle si possible).
- La limitation 1 appel/s et le cache sont **par processus** (cache mémoire local) : avec plusieurs workers gunicorn,
  le débit cumulé peut dépasser 1 appel/s. À traiter en phase 10 (cache partagé).
- Le libellé dépend de la qualité des données OSM du quartier (ex. « Université du Bénin » est un lieu, pas un quartier).

---

## 3. Brevo : SMS de connexion (OTP)

| | |
|---|---|
| Rôle | Envoyer le code OTP de connexion citoyenne |
| Lien officiel | https://www.brevo.com (compte) ; API : https://developers.brevo.com/docs/transactional-sms-endpoints |
| URL | `https://api.brevo.com/v3/transactionalSMS/send` (variable `SMS_API_URL`) ; l'ancien `/v3/transactionalSMS/sms` est déprécié |
| Méthode | `POST`, JSON, en-tête `api-key: <SMS_API_KEY>` |
| Paramètres | `sender` (`SMS_SENDER_ID`, 11 car. max), `recipient` (`22890123456` : indicatif sans « + »), `content`, `type=transactional`, `tag=accimap-otp`, `unicodeEnabled` seulement si le texte sort de l'alphabet GSM |
| Réponse attendue | HTTP 201 `{"messageId": …}` ; erreurs 400/401/402 `{"code", "message"}` |
| Code | `accounts/sms.py` (`BrevoSMSBackend`), délai `SMS_TIMEOUT_SECONDS` (10 s) |
| Variables | `SMS_BACKEND=accounts.sms.BrevoSMSBackend` (défaut), `SMS_API_KEY`, `SMS_SENDER_ID`, `OTP_DEV_MODE=False` |
| Prérequis | crédits SMS (packs de 100, sans expiration), Sender ID approuvé pour le Togo (pas de nom générique type INFO/SMS) |

**Deux modes (variables d'environnement uniquement)**

- *Simulation / soutenance* : `SMS_BACKEND=accounts.sms.ConsoleSMSBackend`, `OTP_DEV_MODE=True`. `ConsoleSMSBackend`
  reçoit le même numéro et le même message que Brevo et renvoie un identifiant `sim-…` ; aucun appel réseau, aucun
  crédit, aucune clé ; message écrit dans la console (numéro masqué) et code présenté dans un bandeau « Mode
  développement — SMS simulé ». Le cycle OTP (génération, empreinte, expiration, tentatives, renvoi, plafond,
  validation, connexion) est celui de la production.
- *Production* : `SMS_BACKEND=accounts.sms.BrevoSMSBackend`, `OTP_DEV_MODE=False`, `SMS_API_KEY`, `SMS_SENDER_ID`.

**Sélection automatique** : `OTP_DEV_MODE=True` -> SMS simulé, quel que soit `SMS_BACKEND` ; `OTP_DEV_MODE=False` ->
`SMS_BACKEND` (Brevo par défaut), le backend simulé étant alors refusé (y compris s'il est appelé directement). Le backend console est refusé hors mode dev. Pendant `manage.py test`, la clé est vidée : aucun test ne peut
envoyer de vrai SMS.

**Sécurité** : ni la clé, ni le contenu du message (code), ni le numéro complet ne sont journalisés (numéro masqué
`+228****56`, `messageId`). Une erreur Brevo donne un message clair dans les logs (ex. `HTTP 402 : not_enough_credits`)
et « Le SMS n'a pas pu être envoyé » à l'utilisateur, sans enregistrer de code.

**Résultat obtenu** : 21 tests Brevo (API simulée) et 27 tests de simulation (réseau coupé) réussis ;
`manage.py sms_test +228…` en simulation : cycle complet OK. **Aucun SMS réel envoyé** : pas de clé ni de crédits
(choix du projet pour la phase prototype). Vrai test Brevo : `python manage.py sms_test --brevo --account`, puis
`python manage.py sms_test --brevo +228XXXXXXXX`.

## 4. Stockage des photos

Pas de service externe : `FileSystemStorage` (disque local, `MEDIA_ROOT`). Photos ré-encodées sans EXIF, servies
uniquement par une vue protégée (aucune route `/media/`). Sur Render, le disque d'un service web est **éphémère** :
les photos seraient perdues à chaque redéploiement. Changement de stockage prévu par `MEDIA_STORAGE_BACKEND`
(phase 10 : disque persistant Render ou stockage objet).

## 5. Render

Non configuré (phase 10). Fournira l'hébergement web et PostgreSQL avec l'extension PostGIS.
