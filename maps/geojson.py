"""
Sérialisation PUBLIQUE des signalements en GeoJSON.

C'est le SEUL endroit qui décide ce que le public peut voir. Principe : liste blanche.
Un champ n'est exposé que s'il est écrit explicitement ici ; ajouter un champ au modèle
ne le rend donc jamais public par accident.

Exposé : type, gravité, date et heure de l'accident, blessés, décès, et la description
SEULEMENT si PUBLIC_MAP_SHOW_DESCRIPTION est activé.

Jamais exposé : utilisateur, téléphone, mode anonyme/identifié, identifiant, référence
(numérotation séquentielle : elle révélerait le volume des signalements non publics),
date de création, photo, statut interne, note ou auteur de la vérification.
Un signalement anonyme et un signalement identifié ont strictement la même forme.
"""
import json

from django.conf import settings
from django.contrib.gis.db.models.functions import AsGeoJSON

from reports.models import AccidentReport

# Précision des coordonnées : 6 décimales (≈ 11 cm), comme à l'enregistrement.
GEOJSON_PRECISION = 6


def public_queryset(filter_form=None):
    """Signalements publics (statut Vérifié uniquement), éventuellement filtrés."""
    queryset = AccidentReport.objects.public()
    if filter_form is not None:
        queryset = filter_form.apply(queryset)
    return queryset


def public_feature_collection(queryset, limit=None):
    """
    GeoJSON FeatureCollection calculé par PostGIS à partir du PointField `location`
    (source géographique de référence).
    """
    limit = limit or settings.PUBLIC_MAP_MAX_FEATURES
    show_description = settings.PUBLIC_MAP_SHOW_DESCRIPTION

    rows = list(
        queryset.annotate(geometry_json=AsGeoJSON("location", precision=GEOJSON_PRECISION))
        .only("accident_type", "severity", "accident_date", "accident_time",
              "injured_count", "death_count", "description")
        .order_by("-accident_date", "-accident_time", "pk")[: limit + 1]
    )
    truncated = len(rows) > limit
    rows = rows[:limit]

    features = []
    for report in rows:
        properties = {
            "type": report.accident_type,
            "type_label": report.get_accident_type_display(),
            "severity": report.severity,
            "severity_label": report.get_severity_display(),
            "date": report.accident_date.isoformat(),
            "time": report.accident_time.strftime("%H:%M"),
            "injured": report.injured_count,
            "deaths": report.death_count,
        }
        if show_description:
            properties["description"] = report.description
        features.append(
            {
                "type": "Feature",
                "geometry": json.loads(report.geometry_json),
                "properties": properties,
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {"count": len(features), "truncated": truncated},
    }
