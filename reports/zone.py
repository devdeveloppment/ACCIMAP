"""
Zone de couverture d'ACCIMAP : sert UNIQUEMENT à avertir l'utilisateur lorsqu'une
position semble hors de la zone couverte. Elle ne doit JAMAIS servir à refuser un
signalement ni à modifier des coordonnées.

Source de la zone (une seule fonction à connaître : get_coverage_zone) :

1. Si COVERAGE_ZONE_GEOJSON est défini : polygone (ou multipolygone) lu dans ce
   fichier GeoJSON. C'est ainsi que sera branchée la limite officielle du District
   Autonome du Grand Lomé, sans aucune modification de code (voir data/README.md).
   COVERAGE_ZONE_IS_OFFICIAL indique si ce fichier est une donnée officielle.
2. Sinon : rectangle INDICATIF ci-dessous, présenté comme approximatif et non officiel.
"""
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, Point, Polygon
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver

logger = logging.getLogger(__name__)

# Rectangle GROSSIER (lon_min, lat_min, lon_max, lat_max) englobant largement Lomé.
# ATTENTION : valeurs approximatives choisies par l'équipe de développement, NON issues
# d'une source officielle. Voir data/README.md pour les remplacer par un vrai polygone.
INDICATIVE_BOUNDS = (1.15, 6.08, 1.40, 6.32)
INDICATIVE_NAME = "Zone indicative autour de Lomé"
OFFICIAL_DEFAULT_NAME = "District Autonome du Grand Lomé"

INDICATIVE_DISCLAIMER = (
    "La zone affichée est indicative : elle est approximative et ne constitue pas "
    "une limite administrative officielle."
)


@dataclass(frozen=True)
class CoverageZone:
    name: str
    is_official: bool
    geometry: GEOSGeometry

    def contains(self, latitude, longitude):
        return self.geometry.prepared.contains(
            Point(longitude, latitude, srid=settings.ACCIMAP_SRID)
        )

    @property
    def label(self):
        if self.is_official:
            return f"Zone de couverture : {self.name}"
        return "Zone indicative approximative (non officielle)"

    @property
    def disclaimer(self):
        return "" if self.is_official else INDICATIVE_DISCLAIMER

    def as_feature(self):
        """GeoJSON affichable par Leaflet."""
        return {
            "type": "Feature",
            "properties": {"name": self.name, "is_official": self.is_official},
            "geometry": json.loads(self.geometry.geojson),
        }


def _load_geojson_geometry(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImproperlyConfigured(
            f"COVERAGE_ZONE_GEOJSON : fichier illisible ou invalide ({path}) : {exc}"
        ) from exc

    # Accepte une FeatureCollection, une Feature ou une géométrie seule.
    if data.get("type") == "FeatureCollection":
        geometries = [f.get("geometry") for f in data.get("features", []) if f.get("geometry")]
    elif data.get("type") == "Feature":
        geometries = [data.get("geometry")]
    else:
        geometries = [data]

    polygons = []
    for geometry in geometries:
        geos = GEOSGeometry(json.dumps(geometry), srid=settings.ACCIMAP_SRID)
        if geos.geom_type == "Polygon":
            polygons.append(geos)
        elif geos.geom_type == "MultiPolygon":
            polygons.extend(list(geos))
        else:
            raise ImproperlyConfigured(
                f"COVERAGE_ZONE_GEOJSON doit contenir des polygones (reçu : {geos.geom_type})."
            )
    if not polygons:
        raise ImproperlyConfigured("COVERAGE_ZONE_GEOJSON ne contient aucun polygone.")

    merged = polygons[0]
    for polygon in polygons[1:]:
        merged = merged.union(polygon)
    merged.srid = settings.ACCIMAP_SRID
    return merged


@lru_cache(maxsize=1)
def get_coverage_zone():
    """Retourne la zone de couverture active (mise en cache)."""
    geojson_path = settings.COVERAGE_ZONE_GEOJSON
    if geojson_path:
        path = Path(geojson_path)
        if not path.is_absolute():
            path = settings.BASE_DIR / path
        geometry = _load_geojson_geometry(path)
        is_official = settings.COVERAGE_ZONE_IS_OFFICIAL
        default_name = OFFICIAL_DEFAULT_NAME if is_official else "Zone de couverture (fichier GeoJSON)"
        return CoverageZone(settings.COVERAGE_ZONE_NAME or default_name, is_official, geometry)

    geometry = Polygon.from_bbox(INDICATIVE_BOUNDS)
    geometry.srid = settings.ACCIMAP_SRID
    return CoverageZone(INDICATIVE_NAME, False, geometry)


@receiver(setting_changed)
def _reset_zone_cache(sender, setting, **kwargs):
    if setting.startswith("COVERAGE_ZONE") or setting == "BASE_DIR":
        get_coverage_zone.cache_clear()


def annotate_in_zone(queryset):
    """
    Ajoute `in_zone` (vrai/faux) à chaque signalement, calculé À LA VOLÉE par PostGIS
    (ST_Within) avec la zone de couverture ACTUELLE. Rien n'est stocké : si la zone
    change (polygone officiel), tous les résultats suivent immédiatement.
    """
    from django.db.models import BooleanField, Case, Value, When

    return queryset.annotate(
        in_zone=Case(
            When(location__within=get_coverage_zone().geometry, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
    )
