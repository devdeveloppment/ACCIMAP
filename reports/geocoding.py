"""
Indication lisible d'une position (« Quartier, Ville ») par géocodage inverse OpenStreetMap (Nominatim).

PRINCIPES
  * Facultatif et jamais bloquant : en cas d'échec (réseau, service lent, désactivé) on retourne None et le
    parcours de signalement fonctionne normalement avec les seules coordonnées.
  * Le NAVIGATEUR ne contacte jamais le service : c'est le serveur ACCIMAP qui le fait (l'adresse IP de
    l'utilisateur n'est pas transmise). Seules les coordonnées de la position sont envoyées.
  * Politesse envers le service public : cache 24 h par position (~11 m), au plus 1 appel par seconde pour
    l'ensemble du site (tous processus confondus, via le cache partagé), délai maximal de 3 s, identification par User-Agent.
  * Désactivable : REVERSE_GEOCODING_ENABLED=False. Pour un usage intensif, utiliser votre propre serveur
    Nominatim (GEOCODER_URL), le serveur public d'OpenStreetMap ne convenant pas à un trafic important.
"""
import json
import logging
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 3
MIN_INTERVAL_SECONDS = 1.0
CACHE_SUCCESS_SECONDS = 24 * 3600
CACHE_FAILURE_SECONDS = 60
_MISSING = object()

# Limitation GLOBALE (tous les processus gunicorn) : une clé du cache partagé, posée pour 1 s.
THROTTLE_KEY = "geocode:throttle"

# Éléments d'adresse OSM, du plus fin au plus large.
LOCAL_KEYS = ("neighbourhood", "quarter", "suburb", "city_district", "hamlet", "village")
CITY_KEYS = ("city", "town", "municipality", "county")


def format_place(payload):
    """Construit « Quartier, Ville » à partir de la réponse Nominatim (ou None)."""
    address = (payload or {}).get("address") or {}
    local = next((address[key] for key in LOCAL_KEYS if address.get(key)), None)
    city = next((address[key] for key in CITY_KEYS if address.get(key)), None)
    parts = [part for part in (local, city) if part]
    if len(parts) == 2 and parts[0] == parts[1]:
        parts = parts[:1]
    return ", ".join(parts) or None


def _throttled():
    """Vrai si un appel a eu lieu il y a moins d'une seconde, dans N'IMPORTE QUEL processus.
    cache.add n'écrit que si la clé est absente : un seul appel par seconde pour tout le site."""
    return not cache.add(THROTTLE_KEY, 1, MIN_INTERVAL_SECONDS)


def reverse_geocode(latitude, longitude):
    """Retourne un libellé court, ou None. Ne lève jamais d'exception."""
    if not settings.REVERSE_GEOCODING_ENABLED:
        return None

    key = f"geocode:{latitude:.4f}:{longitude:.4f}"
    cached = cache.get(key, _MISSING)
    if cached is not _MISSING:
        return cached
    if _throttled():
        return None   # trop rapproché : on ne met pas en cache, un appel ultérieur pourra aboutir

    query = urlencode({
        "format": "jsonv2", "lat": f"{latitude:.6f}", "lon": f"{longitude:.6f}",
        "zoom": 16, "addressdetails": 1, "accept-language": "fr",
    })
    contact = f" ({settings.GEOCODER_CONTACT})" if settings.GEOCODER_CONTACT else ""
    request = Request(
        f"{settings.GEOCODER_URL}?{query}",
        headers={"User-Agent": f"ACCIMAP-prototype/1.0{contact}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 (URL fixée par la configuration)
            payload = json.loads(response.read().decode("utf-8"))
        place = None if payload.get("error") else format_place(payload)
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.info("Géocodage inverse indisponible : %s", exc.__class__.__name__)
        cache.set(key, None, CACHE_FAILURE_SECONDS)
        return None

    cache.set(key, place, CACHE_SUCCESS_SECONDS if place else CACHE_FAILURE_SECONDS)
    return place
