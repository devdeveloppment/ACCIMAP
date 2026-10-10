# ACCIMAP

**Plateforme web participative de collecte et de cartographie des accidents de la circulation**
Prototype académique, District Autonome du Grand Lomé (Togo).

Les citoyens signalent un accident sans créer de compte, le signalement est géolocalisé et enregistré
dans PostgreSQL/PostGIS, un administrateur le vérifie, et les signalements **vérifiés**
apparaissent sur une carte publique (marqueurs groupés, heatmap, filtres).

> Statut : phases 1 à 7 terminées (fondations, authentification OTP, signalement en 4 étapes, carte publique,
> accueil, espace administrateur, exports CSV/Excel/PDF, Django Admin). **À venir** : `seed_demo` et tests de
> synthèse (phase 8), déploiement Render (phase 9).

## Fonctionnalités actuelles

- Connexion par **numéro de téléphone + code OTP** (sans e-mail ni mot de passe), couche SMS interchangeable.
- **Signalement public, sans compte, téléphone ni code OTP obligatoire** (`/report/`; `/anonymous-report/`
  reste un alias rétrocompatible), en **4 étapes** : **1. Type** d'accident (8 cartes visuelles) et cause
  facultative parmi six facteurs distincts · **2. Lieu** (carte, position GPS trouvée
  automatiquement, quartier indiqué, repère déplaçable, confirmation) · **3. Détails** (date, heure, gravité,
  véhicules, blessés, décès, description, photo) · **4. Récapitulatif** puis envoi. Conçu d'abord pour le téléphone
  (boutons d'action fixés en bas de l'écran) ; **sans JavaScript**, toutes les étapes s'affichent et le formulaire
  reste utilisable.
- Les types décrivent la **nature** de l'accident (collision, renversement, sortie de voie, perte de contrôle,
  intersection, carambolage, piéton, autre), jamais le véhicule. Les causes sont une sélection facultative,
  distincte du type. Listes modifiables dans `reports/choices.py`.
- Localisation par **GPS du navigateur** ou **clic sur la carte** ; latitude/longitude visibles avant l'envoi ;
  gestion du refus de permission, du GPS indisponible, du délai dépassé et de la faible précision.
- Validation **côté serveur** des coordonnées, des dates, des nombres et des photos.
- Photos **ré-encodées avant stockage** : métadonnées EXIF (GPS, appareil, auteur) supprimées.
- **Carte publique** (`/map/`) : Leaflet + OpenStreetMap, données chargées dynamiquement depuis PostGIS en GeoJSON,
  clustering, heatmap, filtres (période, type, gravité).
- Statuts **En attente / Vérifié / Rejeté** : seuls les signalements **Vérifiés** sont publics.
- **Espace administrateur** (`/dashboard/`) : statistiques calculées en SQL avec graphiques Chart.js, liste des
  signalements avec recherche et filtres (statut, mode identifié/anonyme, période, type, gravité, zone dans/hors
  couverture calculée à la volée), page de détail (carte, photo protégée), changement de statut, note, modification,
  suppression avec confirmation, carte administrateur (tous les statuts), gestion des utilisateurs et des permissions.
- **Exports** (`/dashboard/exports/`) en **CSV**, **Excel (.xlsx)** et **PDF**, qui reprennent les filtres actifs
  du tableau de bord. **Aucun numéro de téléphone** n'y figure, jamais.
- **Django Admin** (`/admin/`) comme outil de secours : signalements (liste, filtres dont zone dans/hors couverture,
  recherche, actions de statut et d'export), utilisateurs, journal d'historique ; mêmes permissions que le
  tableau de bord.
- Icônes : une seule famille (**Lucide**, SVG intégrés) ; aucun emoji dans l'interface (vérifié par un test).

## Technologies

Python 3.12 · Django 5.2 (GeoDjango) · PostgreSQL 16 + PostGIS 3 · Bootstrap 5 · Leaflet 1.9
(+ markercluster, leaflet.heat) · Chart.js · Lucide (icônes) · OpenStreetMap · Pillow · openpyxl (Excel) ·
ReportLab (PDF) · gunicorn + WhiteNoise (production).
Bootstrap, Leaflet, Chart.js et les icônes sont **embarqués** dans `static/vendor/` : aucun CDN n'est nécessaire
(sauf la carte d'édition du Django Admin, voir « Limites »).

## Architecture

```
config/      réglages, URLs racine, accueil, à propos, sonde /healthz/
accounts/    utilisateur (téléphone), OTP, couche SMS (sms.py), rôles et permissions (roles.py)
reports/     modèle AccidentReport, formulaires, validation des photos, zone de couverture, filtres
maps/        carte publique : page + API GeoJSON (liste blanche des champs publics)
dashboard/   espace administrateur : statistiques, signalements, carte, utilisateurs et permissions
exports/     exports CSV / XLSX / PDF (phase 7)
config/     réglages, URLs racine, pages publiques et contact SMTP
templates/   gabarits  ·  static/ fichiers statiques  ·  scripts/ vérifications navigateur
data/        données géographiques optionnelles (zone de couverture)
```

## Installation locale

Prérequis : Python 3.12, PostgreSQL 14+ avec **PostGIS**, bibliothèques **GDAL/GEOS/PROJ**.

- Ubuntu/Debian : `sudo apt install postgresql postgresql-16-postgis-3 gdal-bin libgdal-dev`
- macOS : `brew install postgresql postgis gdal`
- Windows : PostgreSQL (EDB) + PostGIS via Stack Builder, puis GDAL/GEOS (OSGeo4W) ;
  renseigner `GDAL_LIBRARY_PATH` et `GEOS_LIBRARY_PATH` dans `.env`.

```bash
createdb accimap
psql -d accimap -c "CREATE EXTENSION postgis;"
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # puis renseigner SECRET_KEY et la base de données
python manage.py migrate
python manage.py createsuperuser     # demande un numéro de téléphone et un mot de passe
python manage.py runserver
```

Port 8000 déjà occupé (« You don't have permission to access that port », par ex. un conteneur Docker) :
définir `RUNSERVER_PORT=8001` dans `.env`, ou lancer `python manage.py runserver 8001`.

Clé secrète : `python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"`.

## Configuration (`.env`)

Toutes les variables sont documentées dans `.env.example`. Aucun secret ne doit être versionné.

| Variable | Rôle |
|---|---|
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | Réglages Django (`DEBUG` vaut `False` par défaut) |
| `DATABASE_URL` ou `DB_*` | Connexion PostgreSQL/PostGIS |
| `OTP_DEV_MODE` | `True` : SMS simulé, code en console et dans un bandeau « Mode développement — SMS simulé » ; `False` : SMS réels |
| `SMS_BACKEND`, `SMS_API_KEY`, `SMS_SENDER_ID` | `ConsoleSMSBackend` (simulation) ou `BrevoSMSBackend` (réel), clé API, Sender ID (voir ci-dessous) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_TIMEOUT_SECONDS` | Paramètres serveur de l'envoi du formulaire `/contact/` (Gmail : `smtp.gmail.com`, port `587`, TLS) |
| `CONTACT_FROM_EMAIL`, `CONTACT_RECEIVER_EMAIL` | Adresse expéditrice authentifiée et adresse de réception (par défaut `atou1926@gmail.com`) |
| `CONTACT_RATE_LIMIT`, `CONTACT_RATE_WINDOW_SECONDS` | Limitation anti-spam des envois par adresse IP (3 par heure par défaut) |
| `PUBLIC_MAP_SHOW_DESCRIPTION` | Publier la description libre dans les popups publics (`False` par défaut) |
| `ADMIN_MAP_MAX_FEATURES` | Nombre maximal de points sur la carte administrateur (10000 par défaut) |
| `COVERAGE_ZONE_GEOJSON`, `COVERAGE_ZONE_IS_OFFICIAL`, `COVERAGE_ZONE_NAME` | Zone de couverture (voir `data/README.md`) |
| `MEDIA_STORAGE_BACKEND` | Stockage des photos (disque local par défaut) |

Le formulaire de contact ne rapporte une réussite que lorsque le backend SMTP confirme l'envoi.
Configurez les paramètres SMTP dans les variables d'environnement du serveur (ou dans `.env` en local,
qui est ignoré par Git) ; n'ajoutez jamais le mot de passe SMTP à `.env.example`, au frontend ou au dépôt.
Pour Gmail, créez et utilisez un mot de passe d'application après avoir activé la validation en deux étapes.

## Tests

```bash
python manage.py check
python manage.py test            # tests unitaires et d'intégration (PostGIS requis)
```

Vérifications dans un vrai navigateur (Chromium, formats smartphone et ordinateur). Outils de développement
(`pypdf` pour lire les PDF des tests, `playwright`) : `pip install -r requirements-dev.txt && playwright install chromium`,
puis, serveur lancé (`OTP_DEV_MODE=True`) :

```bash
python scripts/e2e_auth_flow.py      # connexion OTP, déconnexion, responsive
python scripts/e2e_report_flow.py    # parcours en 4 étapes : identifié/anonyme, GPS, hors zone, photo EXIF, sans JavaScript
python scripts/e2e_map_flow.py       # carte publique : visibilité, confidentialité, clusters, heatmap, filtres
# Espace administrateur : le staff et le superutilisateur se connectent par l'espace privé (identifiant + mot de
# passe) ; les profils citoyens, eux, par OTP et plusieurs fois. Le serveur doit donc être lancé SANS délai
# anti-renvoi d'OTP ; un format à la fois (les scénarios modifient des données) :
OTP_RESEND_DELAY_SECONDS=0 OTP_MAX_REQUESTS_PER_HOUR=100 python manage.py runserver 8000
E2E_VIEWPORT=desktop python scripts/e2e_dashboard_flow.py
E2E_VIEWPORT=mobile  python scripts/e2e_dashboard_flow.py
E2E_VIEWPORT=desktop python scripts/e2e_exports_admin_flow.py   # exports téléchargés, filtres, Django Admin
E2E_VIEWPORT=mobile  python scripts/e2e_exports_admin_flow.py
# Parcours complet (phase 8) : citoyen OTP -> signalement -> validation -> carte publique -> statistiques -> exports
python scripts/e2e_full_journey.py
# Services externes RÉELS (phase 9) : tuiles OpenStreetMap et 2-3 appels Nominatim. Usage ponctuel uniquement.
python scripts/check_external_services.py      # SKIP_NOMINATIM=1 pour ne tester que les tuiles
```

Ces scripts créent leurs propres données de test et les suppriment à la fin.

## Authentification par OTP et SMS

Le cycle OTP est toujours RÉEL : code généré par `secrets`, seule son empreinte HMAC est stockée, expiration,
tentatives maximales, délai de renvoi, plafond horaire, validation et connexion (`accounts/otp.py`). Seul le
**transport** du SMS change selon le mode, choisi uniquement par les variables d'environnement :

| | Mode simulation (développement, soutenance) | Mode production |
|---|---|---|
| Variables | `SMS_BACKEND=accounts.sms.ConsoleSMSBackend`, `OTP_DEV_MODE=True` | `SMS_BACKEND=accounts.sms.BrevoSMSBackend`, `OTP_DEV_MODE=False`, `SMS_API_KEY`, `SMS_SENDER_ID` |
| SMS | simulé : aucun SMS réel, aucun crédit, aucune clé, hors ligne | réel, via l'API officielle **Brevo** (Togo +228) |
| Code | console du serveur (encadré « SMS simulé », numéro masqué) et bandeau « Mode développement — SMS simulé » sur la page de saisie | uniquement sur le téléphone ; jamais affiché ni journalisé |
| Prérequis | aucun | compte Brevo, **crédits SMS**, **Sender ID approuvé pour le Togo**, clé API (jamais dans Git) |

Avec `OTP_DEV_MODE=True`, aucun SMS réel ne peut partir, quel que soit `SMS_BACKEND`. Avec `OTP_DEV_MODE=False`, le
backend simulé est refusé et le code n'apparaît nulle part : si l'envoi échoue, l'utilisateur voit « Le SMS n'a pas
pu être envoyé » et aucun code n'est enregistré. `python manage.py check` signale les configurations incohérentes
(backend inexistant, Brevo sans clé, fournisseur réel ignoré en simulation, simulation avec `DEBUG=False`).

Le mode simulation permet à quiconque de se connecter avec n'importe quel numéro (le code s'affiche) : il est réservé
aux tests et à la démonstration, jamais à une utilisation réelle. L'espace administrateur n'en dépend pas (mot de passe).

```bash
python manage.py sms_test --account              # simulation : configuration ; production : clé + crédits Brevo
python manage.py sms_test +228XXXXXXXX           # cycle complet avec le fournisseur configuré
python manage.py sms_test --brevo +228XXXXXXXX   # force un VRAI SMS Brevo (1 crédit), code saisi au clavier
```

Autre fournisseur : une classe héritant de `BaseSMSBackend` (mode d'emploi en tête de `accounts/sms.py`).

## Espace d'administration privé

L'administration est un **espace privé, séparé de l'interface citoyenne** :

- **Interface publique** (citoyens) : accueil, carte, à propos, signalement, connexion par **numéro + code OTP**.
  Elle ne contient **aucun** lien ni élément d'administration, quel que soit le profil connecté.
- **Espace privé** : `/dashboard/login/`, connexion par **identifiant administrateur + mot de passe**. L'identifiant
  est le numéro du compte (champ identifiant de Django, sans changement de modèle). Gabarit propre (en-tête
  « Administration », sans barre citoyenne) avec vue globale : signalements, statistiques, carte, validation/rejet,
  exports, utilisateurs, historique (journal), administration avancée (Django Admin).
- **Règle serveur** (chaque vue, `accounts/decorators.py`) : un citoyen reçoit **403** ; un visiteur, ou un compte
  staff connecté par l'OTP citoyen, est renvoyé vers la connexion privée (**401** pour les API) ; à l'intérieur,
  les permissions (voir, modifier, supprimer, superutilisateur) s'appliquent comme avant. La session administrateur
  est marquée et liée à l'utilisateur, et expire après `ADMIN_SESSION_AGE` secondes (8 h par défaut).
- Le **Django Admin** (`/admin/`) exige la même session administrateur et utilise la même page de connexion.
- **Sécurité de la connexion** : message d'erreur unique (aucune divulgation de l'existence d'un compte), blocage de
  15 min après 5 échecs pour un identifiant (`ADMIN_LOGIN_MAX_ATTEMPTS`, `ADMIN_LOGIN_LOCK_SECONDS`), protection CSRF,
  mot de passe jamais renvoyé ni journalisé, règles de robustesse de Django.
- **Mots de passe** : le premier superutilisateur se crée avec `python manage.py createsuperuser` (numéro + mot de
  passe). Un superutilisateur définit ensuite le mot de passe d'un compte staff depuis sa fiche (« Utilisateurs ») ;
  chaque administrateur peut changer le sien (« Mot de passe »). Un compte rétrogradé en utilisateur simple perd son
  mot de passe (et sa session administrateur).

## Niveaux d'accès et permissions

Trois profils, fondés sur les permissions Django (`accounts/roles.py`) et appliqués sur **chaque** vue
(`accounts/decorators.py`). Un accès refusé renvoie une erreur 403 sans rien révéler de l'existence de l'objet.

| Action | Utilisateur | Staff | Staff « lecture seule » | Superutilisateur |
|---|---|---|---|---|
| Tableau de bord, statistiques, liste, détail, carte admin, photos | non | oui | oui | oui |
| Changer le statut, ajouter une note, modifier un signalement | non | oui (permission *modifier*) | non | oui |
| Supprimer un signalement | non | oui (permission *supprimer*) | non | oui |
| Gérer les utilisateurs, activer/désactiver, changer les permissions | non | **non** | non | **oui** |
| Exporter en CSV / Excel / PDF (sans téléphone) | non | oui (permission *voir*) | oui | oui |
| Django Admin : signalements (liste, fiche, actions) | non | selon ses permissions | consultation seule | oui |
| Django Admin : utilisateurs et journal d'historique | non | **non** | non | **oui** |
| Voir un numéro de téléphone **complet** | non | non | non | oui, **uniquement** dans « Utilisateurs » |

- Le **staff** consulte toujours (permission *voir*) ; *modifier* et *supprimer* sont des permissions distinctes,
  réglables par un superutilisateur depuis `/dashboard/users/<id>/` (par défaut : toutes accordées).
- Un superutilisateur **ne peut ni se désactiver ni réduire ses propres droits** (anti-verrouillage).
- Une désactivation ou un changement de droits s'applique **immédiatement**, sur les sessions déjà ouvertes.
- Pour donner un accès, la personne doit d'abord s'être connectée une fois par OTP (création du compte) ; le
  superutilisateur lui attribue ensuite un niveau staff **et** un mot de passe d'administration.
- Les actions sensibles (statut, modification, suppression, changement d'accès) sont tracées dans l'historique de
  Django (`LogEntry`), sans numéro de téléphone complet.
- Dans l'espace staff, un déclarant identifié n'apparaît que sous forme **masquée** (`+228****56`) ; un
  superutilisateur dispose en plus d'un lien vers le compte.
- **Photos** : jamais d'URL publique (aucune route `/media/`). Elles sont servies uniquement par une vue protégée
  (`/dashboard/reports/<id>/photo/`), avec `Cache-Control: no-store` ; elles sont effacées du stockage à la
  suppression du signalement.
- Toutes les pages d'administration sont marquées `no-store` et `noindex`.
- **Exports** : confidentialité par construction. Un export ne charge jamais l'utilisateur d'un signalement, donc
  aucun numéro ne peut s'y glisser ; la note interne et l'identité de la personne qui a traité le signalement en sont
  exclues. Les cellules commençant par `= + - @` sont préfixées d'une apostrophe (**injection de formules**
  Excel/LibreOffice neutralisée). Chaque export est journalisé (qui, format, nombre de lignes, filtres). Au-delà de
  `EXPORT_MAX_ROWS` (20 000), CSV et Excel sont refusés ; le PDF est limité à `EXPORT_PDF_MAX_ROWS` (500) avec mention
  explicite dans le document. La description, texte libre saisi par le déclarant, est incluse pour le staff.
- **Django Admin** : les relations vers l'utilisateur ne sont pas des champs du formulaire (déclarant et personne
  ayant traité apparaissent masqués, y compris dans l'historique) ; la photo passe par la vue protégée ; l'ajout de
  signalements est désactivé ; les comptes se désactivent plutôt qu'ils ne se suppriment ; le journal est en lecture
  seule. Accès après connexion à l'espace privé (`/dashboard/login/`), jamais par l'OTP citoyen.

## Carte publique : ce qui est visible

| Statut | Carte publique | Espace administrateur |
|---|---|---|
| Vérifié | affiché | affiché |
| En attente | **jamais** | affiché (carte et liste administrateur) |
| Rejeté | **jamais** | affiché (carte et liste administrateur) |

L'API publique (`/map/data/`) n'expose qu'une **liste blanche** de champs (`maps/geojson.py`) :
type, gravité, date et heure de l'accident, nombre de blessés et de décès, coordonnées, et la description
seulement si `PUBLIC_MAP_SHOW_DESCRIPTION=True`. **Jamais** : utilisateur, téléphone, mode anonyme/identifié,
identifiant, référence, date de création, photo, note d'administration.

Les filtres **statut**, **mode identifié/anonyme** et **zone** sont réservés à l'administrateur : les proposer
au public permettrait d'isoler les signalements anonymes. La heatmap représente des *zones à forte
concentration de signalements* ; ce n'est pas une preuve officielle qu'une zone est accidentogène.

## Confidentialité et limites de l'anonymat

**Ce qu'ACCIMAP garantit**

- Un signalement anonyme n'est **rattaché à aucun utilisateur** : le serveur force `user = NULL` et une contrainte
  PostgreSQL (`report_anonymous_has_no_user`) l'impose même en cas de contournement du code.
- Tous les signalements envoyés depuis `/report/` (ou son alias `/anonymous-report/`) restent anonymes, y compris
  pour un utilisateur déjà connecté ; la page de confirmation utilise un jeton signé sans état : **aucun lien
  n'est écrit en session** entre le compte et le signalement.
- Le téléphone n'est jamais affiché publiquement (il apparaît masqué, `+228****56`, à l'utilisateur connecté).
- Les photos sont ré-encodées : position GPS, modèle d'appareil et auteur sont supprimés avant stockage.
- L'authentification reste disponible pour les fonctions qui la nécessitent, notamment l'administration ; elle
  n'est pas un prérequis au signalement public.

**Ce qu'ACCIMAP ne garantit pas** (« anonyme » signifie anonyme *pour le public et pour l'application*, pas invisible)

- **Journaux réseau** : l'adresse IP et le navigateur d'un visiteur peuvent figurer dans les journaux du serveur
  ou de l'hébergeur (proxy, passerelle), indépendamment de l'application.
- **Corrélation temporelle** : l'heure d'enregistrement d'un signalement anonyme peut être rapprochée de l'heure de
  connexion d'un compte par quelqu'un qui a accès à la base. Aucune mesure de brouillage n'est implémentée.
- **Contenu** : le lieu, l'heure, la description et le contenu *visuel* d'une photo (visages, plaques, véhicule, décor)
  peuvent identifier le déclarant. Le nettoyage des métadonnées ne retire pas ce que la photo montre.
- **Position exacte** : une fois le signalement vérifié, ses coordonnées sont publiques à 6 décimales (≈ 11 cm).
- **Administrateurs** : ils voient l'intégralité des signalements (description, photo, coordonnées) et, selon leurs
  droits, la base de données.
- **Numéros de téléphone** : stockés en clair en base (nécessaires à l'envoi des SMS), sans chiffrement applicatif.
- **Pas de limitation par IP** sur l'envoi de signalements anonymes (spam possible) ; les envois d'OTP sont limités
  par numéro, pas par IP.

## Limites connues du prototype

- **Zone de couverture** : un rectangle *indicatif*, approximatif et non officiel, sert uniquement à avertir
  l'utilisateur (jamais à refuser un signalement). La limite officielle du Grand Lomé se branche sans modification de
  code (`data/README.md`).
- **OpenStreetMap** : le serveur de tuiles public convient à un prototype, pas à un usage intensif
  (politique d'utilisation d'OSM) ; prévoir un fournisseur dédié au-delà. Attribution affichée sur la carte.
- **Photos** : stockées sur disque local. Sur un hébergement à disque éphémère (Render, offre gratuite), elles sont
  perdues à chaque redéploiement. L'architecture permet de changer de stockage via `MEDIA_STORAGE_BACKEND`.
- **SMS** : prototype en **SMS simulé** ; le fournisseur réel Brevo est intégré mais exige une clé API, des crédits
  SMS payants et un Sender ID approuvé pour le Togo (aucun SMS réel envoyé à ce jour). Les administrateurs ne dépendent pas des SMS : ils se
  connectent par l'espace privé (identifiant + mot de passe), sans second facteur.
- **Permissions** : au niveau du modèle (voir, modifier, supprimer) et non par signalement ; pas de journal d'audit
  consultable dans le tableau de bord (l'historique est visible dans le Django Admin, configuré en phase 7).
- Photos HEIC non acceptées (JPEG, PNG et WebP uniquement) ; carte publique limitée à 5000 points.
- **Quartier affiché pendant le signalement** : c'est le *serveur* ACCIMAP (jamais le navigateur) qui interroge le
  service public Nominatim d'OpenStreetMap avec les coordonnées de la position, avec cache et limite d'un appel par
  seconde. Facultatif et jamais bloquant ; désactivable (`REVERSE_GEOCODING_ENABLED=False`). Le serveur public ne
  convient pas à un trafic important : prévoir son propre serveur (`GEOCODER_URL`) et renseigner `GEOCODER_CONTACT`.
- **PDF** : la police intégrée (Helvetica) couvre le jeu Windows-1252 ; un caractère hors de ce jeu (par exemple
  certaines lettres des langues locales) devient « ? » dans le PDF, mais reste intact en CSV et Excel.
- **CSV** : séparateur « ; » et UTF-8 avec BOM (ouverture directe dans Excel en français) ; coordonnées avec un point
  décimal pour les outils SIG.
- **Django Admin** : la carte d'édition de la position charge OpenLayers depuis un CDN ; sans accès internet, la fiche
  reste utilisable mais sans carte.
- Les anciens types d'accident (moto, véhicule seul…) ont été convertis par la migration `0003` : « moto » et
  « véhicule seul » deviennent « Autre » (le véhicule n'est pas une nature d'accident). Le type de véhicule n'est plus
  collecté.
- Les données collaboratives ne remplacent pas des statistiques officielles.

## Déploiement sur Render

Prêt à déployer : `Dockerfile` (GeoDjango avec GDAL/GEOS/PROJ), `docker/start.sh` (migrations avec activation de
PostGIS, cache partagé, compte administrateur initial, gunicorn) et `render.yaml` (Blueprint : service web Docker +
PostgreSQL). Image testée en conditions de production (HTTPS, `DEBUG=False`, base PostGIS vierge).
Mode d'emploi pas à pas, variables à saisir et limites de l'offre gratuite : **[docs/DEPLOIEMENT_RENDER.md](docs/DEPLOIEMENT_RENDER.md)**.

## Bibliothèques tierces embarquées

Bootstrap (MIT), Leaflet (BSD-2), Leaflet.markercluster (MIT), leaflet.heat (BSD-2) : licences dans
`static/vendor/*/LICENSE`. Fond de carte © contributeurs OpenStreetMap (ODbL).
