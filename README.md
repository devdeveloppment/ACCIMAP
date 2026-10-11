# ACCIMAP

**Plateforme web participative de collecte et de cartographie des accidents de la circulation**
Prototype académique, District Autonome du Grand Lomé (Togo).

Les citoyens signalent un accident sans créer de compte, le signalement est géolocalisé et enregistré
dans PostgreSQL/PostGIS, un administrateur le vérifie, et les signalements **vérifiés**
apparaissent sur une carte publique (marqueurs groupés, heatmap, filtres).

> Statut : phases 1 à 7 terminées (fondations, signalement public en 4 étapes, carte publique,
> accueil, espace administrateur, exports CSV/Excel/PDF, Django Admin). **À venir** : `seed_demo` et tests de
> synthèse (phase 8), déploiement Render (phase 9).

## Fonctionnalités actuelles

- **Signalement public, sans compte ni authentification** (`/report/`; `/anonymous-report/`
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
config/     réglages, URLs racine, pages publiques et sonde /healthz/
accounts/    comptes administrateurs, rôles et permissions
reports/     modèle AccidentReport, formulaires, validation des photos, zone de couverture, filtres
maps/        carte publique : page + API GeoJSON (liste blanche des champs publics)
dashboard/   espace administrateur : statistiques, signalements, carte, utilisateurs et permissions
exports/     exports CSV / XLSX / PDF (phase 7)
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
| `PUBLIC_MAP_SHOW_DESCRIPTION` | Publier la description libre dans les popups publics (`False` par défaut) |
| `ADMIN_MAP_MAX_FEATURES` | Nombre maximal de points sur la carte administrateur (10000 par défaut) |
| `COVERAGE_ZONE_GEOJSON`, `COVERAGE_ZONE_IS_OFFICIAL`, `COVERAGE_ZONE_NAME` | Zone de couverture (voir `data/README.md`) |
| `MEDIA_STORAGE_BACKEND` | Stockage des photos (disque local par défaut) |

La page Contact ouvre une conversation WhatsApp avec l'équipe ACCIMAP au numéro togolais
`+228 91 54 03 86`. Aucun envoi d'e-mail ni identifiant SMTP n'est requis.

## Tests

```bash
python manage.py check
python manage.py test            # tests unitaires et d'intégration (PostGIS requis)
```

Vérifications dans un vrai navigateur (Chromium, formats smartphone et ordinateur). Outils de développement
(`pypdf` pour lire les PDF des tests, `playwright`) : `pip install -r requirements-dev.txt && playwright install chromium`,
puis serveur lancé :

```bash
python scripts/e2e_report_flow.py    # parcours en 4 étapes : identifié/anonyme, GPS, hors zone, photo EXIF, sans JavaScript
python scripts/e2e_map_flow.py       # carte publique : visibilité, confidentialité, clusters, heatmap, filtres
# Un format à la fois (les scénarios modifient des données) :
python manage.py runserver 8000
E2E_VIEWPORT=desktop python scripts/e2e_dashboard_flow.py
E2E_VIEWPORT=mobile  python scripts/e2e_dashboard_flow.py
E2E_VIEWPORT=desktop python scripts/e2e_exports_admin_flow.py   # exports téléchargés, filtres, Django Admin
E2E_VIEWPORT=mobile  python scripts/e2e_exports_admin_flow.py
# Parcours complet : signalement anonyme -> validation -> carte publique -> statistiques -> exports
python scripts/e2e_full_journey.py
# Services externes RÉELS (phase 9) : tuiles OpenStreetMap et 2-3 appels Nominatim. Usage ponctuel uniquement.
python scripts/check_external_services.py      # SKIP_NOMINATIM=1 pour ne tester que les tuiles
```

Ces scripts créent leurs propres données de test et les suppriment à la fin.

## Accès public et administration

Les pages publiques et le signalement d'accident sont accessibles sans compte ni connexion. L'authentification
reste réservée à l'espace privé des administrateurs (`/dashboard/login/`), protégé par identifiant et mot de passe.

## Espace d'administration privé

L'administration est un **espace privé, séparé de l'interface citoyenne** :

- **Interface publique** : accueil, carte, à propos, signalement anonyme et contact. Aucun compte n'est requis.
- **Espace privé** : `/dashboard/login/`, connexion par **identifiant administrateur + mot de passe**. L'identifiant
  est le numéro du compte (champ identifiant de Django, sans changement de modèle). Gabarit propre (en-tête
  « Administration », sans barre citoyenne) avec vue globale : signalements, statistiques, carte, validation/rejet,
  exports, utilisateurs, historique (journal), administration avancée (Django Admin).
- **Règle serveur** (chaque vue, `accounts/decorators.py`) : un visiteur ou un utilisateur simple est renvoyé vers la
  connexion privée (**401** pour les API) ; à l'intérieur,
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
- Pour donner un accès, le superutilisateur crée le compte, lui attribue un niveau staff et définit un mot de passe
  d'administration.
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
  seule. Accès après connexion à l'espace privé (`/dashboard/login/`).

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
- **Numéros de téléphone des comptes d'administration** : stockés en clair en base et masqués dans les vues qui ne
  nécessitent pas leur affichage complet.
- **Pas de limitation par IP** sur l'envoi de signalements anonymes (spam possible).

## Limites connues du prototype

- **Zone de couverture** : un rectangle *indicatif*, approximatif et non officiel, sert uniquement à avertir
  l'utilisateur (jamais à refuser un signalement). La limite officielle du Grand Lomé se branche sans modification de
  code (`data/README.md`).
- **OpenStreetMap** : le serveur de tuiles public convient à un prototype, pas à un usage intensif
  (politique d'utilisation d'OSM) ; prévoir un fournisseur dédié au-delà. Attribution affichée sur la carte.
- **Photos** : stockées sur disque local. Sur un hébergement à disque éphémère (Render, offre gratuite), elles sont
  perdues à chaque redéploiement. L'architecture permet de changer de stockage via `MEDIA_STORAGE_BACKEND`.
- **Authentification des administrateurs** : l'espace privé utilise identifiant et mot de passe, sans second facteur.
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
