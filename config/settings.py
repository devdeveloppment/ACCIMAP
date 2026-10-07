"""
Configuration Django du projet ACCIMAP.

Toute valeur sensible ou dépendante de l'environnement (clé secrète, base de
données, SMS, hôtes autorisés...) est lue depuis les variables d'environnement
(fichier .env en local, tableau de bord Render en production).
Voir .env.example pour la liste complète.
"""
import os
import sys
import warnings
from pathlib import Path

import dj_database_url
from django.contrib.messages import constants as message_constants
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Charge le fichier .env s'il existe (inoffensif en production).
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Helpers de lecture des variables d'environnement
# ---------------------------------------------------------------------------
def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ImproperlyConfigured(f"La variable {name} doit être un entier (reçu : {raw!r}).")


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Sécurité de base
# ---------------------------------------------------------------------------
# DEBUG est désactivé par défaut : il faut l'activer explicitement en local.
DEBUG = env_bool("DEBUG", False)

SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        # Clé éphémère : les sessions sont perdues à chaque redémarrage.
        # Définissez SECRET_KEY dans .env pour éviter cela.
        SECRET_KEY = get_random_secret_key()
    else:
        raise ImproperlyConfigured(
            "SECRET_KEY est obligatoire lorsque DEBUG=False. "
            "Définissez-la dans les variables d'environnement."
        )

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Render fournit automatiquement le nom d'hôte public du service.
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "dashboard.admin_apps.AccimapAdminConfig",  # Django Admin, réservé à la session administrateur
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",  # GeoDjango (PostGIS)
    # Applications ACCIMAP
    "accounts",   # utilisateurs, authentification par téléphone + OTP
    "reports",    # signalements d'accidents (modèle principal)
    "dashboard",  # espace administrateur : stats, gestion
    "maps",       # carte publique, API GeoJSON, heatmap
    "exports",    # exports CSV / XLSX / PDF
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    # Production : WhiteNoise sert les fichiers statiques collectés (gunicorn ne le fait pas).
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "builtins": ["config.ui_tags"],  # {% icon "nom" %} disponible dans tous les gabarits
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ---------------------------------------------------------------------------
# Base de données : PostgreSQL + PostGIS
# ---------------------------------------------------------------------------
POSTGIS_ENGINE = "django.contrib.gis.db.backends.postgis"

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    # Cas Render (ou toute URL du type postgres://user:pass@host:5432/nom).
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=env_bool("DB_SSL_REQUIRE", False),
        )
    }
    DATABASES["default"]["ENGINE"] = POSTGIS_ENGINE
else:
    # Cas variables séparées (développement local).
    DATABASES = {
        "default": {
            "ENGINE": POSTGIS_ENGINE,
            "NAME": os.getenv("DB_NAME", "accimap"),
            "USER": os.getenv("DB_USER", "postgres"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 600,
        }
    }

# Chemins explicites vers GDAL/GEOS (utile surtout sous Windows / macOS).
# Laissez vides sous Linux : Django les détecte automatiquement.
if os.getenv("GDAL_LIBRARY_PATH"):
    GDAL_LIBRARY_PATH = os.getenv("GDAL_LIBRARY_PATH")
if os.getenv("GEOS_LIBRARY_PATH"):
    GEOS_LIBRARY_PATH = os.getenv("GEOS_LIBRARY_PATH")

# Système de coordonnées GPS standard (WGS 84) utilisé pour tous les points.
ACCIMAP_SRID = 4326

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Cache PARTAGÉ entre tous les processus (gunicorn lance plusieurs workers) : blocage anti-force-brute de la
# connexion administrateur, limitation Nominatim (1 appel/s pour tout le site) et résultats de géocodage.
# Table créée par « python manage.py createcachetable » (fait automatiquement au démarrage du conteneur).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "accimap_cache",
    }
}


# ---------------------------------------------------------------------------
# Validation des mots de passe (comptes administrateur)
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Utilisateur personnalisé : le numéro de téléphone est l'identifiant.
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.PhoneModelBackend"]

# Indicatif ajouté aux numéros saisis sans préfixe international (Togo : +228).
DEFAULT_PHONE_COUNTRY_CODE = os.getenv("DEFAULT_PHONE_COUNTRY_CODE", "+228")

# Noms d'URL définis dans accounts/urls.py (Phase 3).
LOGIN_URL = "accounts:login"

# Espace d'administration PRIVÉ : authentification propre (identifiant + mot de passe), distincte de la connexion
# citoyenne par OTP. Une session « administrateur » expire après ADMIN_SESSION_AGE secondes.
ADMIN_SESSION_AGE = env_int("ADMIN_SESSION_AGE", 8 * 3600)
ADMIN_LOGIN_MAX_ATTEMPTS = env_int("ADMIN_LOGIN_MAX_ATTEMPTS", 5)      # échecs avant blocage temporaire
ADMIN_LOGIN_LOCK_SECONDS = env_int("ADMIN_LOGIN_LOCK_SECONDS", 15 * 60)
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"


# Les messages Django utilisent les classes d'alerte Bootstrap.
MESSAGE_TAGS = {message_constants.ERROR: "danger"}


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "fr"
TIME_ZONE = "Africa/Lome"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Fichiers statiques et médias
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Stockage des photos : disque local par défaut (prototype). Pour passer à un
# stockage persistant ou externe (S3, Cloudinary...), changer ce backend et
# ses OPTIONS ci-dessous : le code applicatif n'utilise que l'API de stockage
# Django (jamais MEDIA_ROOT directement), donc rien d'autre à modifier.
MEDIA_STORAGE_BACKEND = os.getenv(
    "MEDIA_STORAGE_BACKEND", "django.core.files.storage.FileSystemStorage"
)

STORAGES = {
    "default": {"BACKEND": MEDIA_STORAGE_BACKEND},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# Photos de signalement (validées côté serveur, voir reports/validators.py).
MAX_UPLOAD_SIZE_MB = env_int("MAX_UPLOAD_SIZE_MB", 5)
ALLOWED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
# Nombre maximal de photos jointes à un même signalement (la 1re est la couverture).
MAX_PHOTOS_PER_REPORT = env_int("MAX_PHOTOS_PER_REPORT", 5)


# ---------------------------------------------------------------------------
# Authentification par téléphone (OTP) et SMS
# ---------------------------------------------------------------------------
# En mode développement, aucun SMS réel n'est envoyé : le code est écrit dans
# la console du serveur. NE JAMAIS activer en production réelle.
OTP_DEV_MODE = env_bool("OTP_DEV_MODE", False)
OTP_LENGTH = env_int("OTP_LENGTH", 6)
OTP_EXPIRY_MINUTES = env_int("OTP_EXPIRY_MINUTES", 5)
OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 5)
OTP_RESEND_DELAY_SECONDS = env_int("OTP_RESEND_DELAY_SECONDS", 60)
# Plafond d'envois par numéro et par heure (évite les abus et les coûts SMS).
OTP_MAX_REQUESTS_PER_HOUR = env_int("OTP_MAX_REQUESTS_PER_HOUR", 5)

# Fournisseur SMS : chemin Python de la classe d'envoi (accounts/sms.py). Utilisé UNIQUEMENT si OTP_DEV_MODE=False
# (en mode développement, le backend console est toujours choisi). Par défaut : Brevo (API officielle).
SMS_BACKEND = os.getenv("SMS_BACKEND", "accounts.sms.BrevoSMSBackend")
SMS_API_KEY = os.getenv("SMS_API_KEY", "")          # clé API Brevo (xkeysib-...) : JAMAIS dans Git
SMS_API_SECRET = os.getenv("SMS_API_SECRET", "")    # non utilisé par Brevo (réservé à d'autres fournisseurs)
SMS_SENDER_ID = os.getenv("SMS_SENDER_ID", "ACCIMAP")   # Sender ID approuvé par Brevo pour le Togo (11 car. max)
SMS_API_URL = os.getenv("SMS_API_URL", "https://api.brevo.com/v3/transactionalSMS/send")
SMS_TIMEOUT_SECONDS = env_int("SMS_TIMEOUT_SECONDS", 10)

if sys.argv[1:2] == ["test"]:
    # Garde-fou : aucun test automatisé ne doit pouvoir envoyer un vrai SMS (payant), même si .env contient une clé.
    SMS_API_KEY = ""

if OTP_DEV_MODE and not DEBUG:
    warnings.warn(
        "OTP_DEV_MODE est actif alors que DEBUG=False : les codes OTP ne sont "
        "pas envoyés par SMS. Acceptable pour une démonstration, "
        "jamais pour une utilisation réelle.",
        stacklevel=1,
    )


# ---------------------------------------------------------------------------
# Carte (centre : Lomé)
# ---------------------------------------------------------------------------
MAP_DEFAULT_CENTER = (6.1725, 1.2314)  # (latitude, longitude)
MAP_DEFAULT_ZOOM = 12

# Fond de carte OpenStreetMap. Le serveur de tuiles public d'OSM convient à un
# prototype ; pour un usage intensif, utiliser un fournisseur dédié (voir README).
MAP_TILE_URL = os.getenv("MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
MAP_TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">Contributeurs OpenStreetMap</a>'

# Carte publique : seuls les signalements « Vérifié » sont affichés (voir
# AccidentReport.objects.public()). Le texte libre de la description peut contenir des
# données personnelles (noms, plaques, numéros) : il n'est PAS publié par défaut.
PUBLIC_MAP_SHOW_DESCRIPTION = env_bool("PUBLIC_MAP_SHOW_DESCRIPTION", False)
PUBLIC_MAP_MAX_FEATURES = env_int("PUBLIC_MAP_MAX_FEATURES", 5000)
ADMIN_MAP_MAX_FEATURES = env_int("ADMIN_MAP_MAX_FEATURES", 10000)

# Exports : au-delà, l'export CSV/Excel est refusé (affiner les filtres) et le PDF est tronqué avec mention.
EXPORT_MAX_ROWS = env_int("EXPORT_MAX_ROWS", 20000)
EXPORT_PDF_MAX_ROWS = env_int("EXPORT_PDF_MAX_ROWS", 500)
EXPORT_PDF_COMPRESS = True

# Zone de couverture d'ACCIMAP. Par défaut : zone INDICATIVE approximative autour
# de Lomé (non officielle), utilisée uniquement pour AVERTIR l'utilisateur, jamais
# pour refuser un signalement. Pour brancher la limite officielle du District
# Autonome du Grand Lomé, voir data/README.md :
#   COVERAGE_ZONE_GEOJSON=data/grand_lome.geojson
#   COVERAGE_ZONE_IS_OFFICIAL=True
COVERAGE_ZONE_GEOJSON = os.getenv("COVERAGE_ZONE_GEOJSON", "")
COVERAGE_ZONE_NAME = os.getenv("COVERAGE_ZONE_NAME", "")
COVERAGE_ZONE_IS_OFFICIAL = env_bool("COVERAGE_ZONE_IS_OFFICIAL", False)

# Indication lisible du lieu (« Quartier, Ville ») dans le parcours de signalement. Le serveur interroge
# Nominatim (OpenStreetMap) ; facultatif, jamais bloquant. Voir reports/geocoding.py.
REVERSE_GEOCODING_ENABLED = env_bool("REVERSE_GEOCODING_ENABLED", True)
GEOCODER_URL = os.getenv("GEOCODER_URL", "https://nominatim.openstreetmap.org/reverse")
GEOCODER_CONTACT = os.getenv("GEOCODER_CONTACT", "")   # adresse de contact exigée par la politique d'usage

# Photos : côté le plus long maximal après ré-encodage (pixels).
PHOTO_MAX_DIMENSION = env_int("PHOTO_MAX_DIMENSION", 1920)


# ---------------------------------------------------------------------------
# Sécurité renforcée en production
# ---------------------------------------------------------------------------
if not DEBUG:
    # Render termine le HTTPS en amont et transmet ce en-tête.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # sonde de santé Render
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 3600)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_CONTENT_TYPE_NOSNIFF = True


# ---------------------------------------------------------------------------
# Logs (sortie console, lisible dans les logs Render)
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {name}: {message}", "style": "{"},
    },
    "filters": {
        "strip_client_error_traceback": {"()": "config.log_filters.StripClientErrorTraceback"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "filters": ["strip_client_error_traceback"],
        },
    },
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
