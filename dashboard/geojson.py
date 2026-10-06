"""GeoJSON de la carte ADMINISTRATEUR : tous les statuts. Jamais exposé au public."""
import json

from django.conf import settings
from django.contrib.gis.db.models.functions import AsGeoJSON

from reports.zone import annotate_in_zone


def admin_feature_collection(queryset):
    limit = settings.ADMIN_MAP_MAX_FEATURES
    rows = list(
        annotate_in_zone(queryset)
        .annotate(geometry_json=AsGeoJSON("location", precision=6))
        .only("reference", "status", "accident_type", "severity", "accident_date",
              "accident_time", "injured_count", "death_count", "is_anonymous")
        .order_by("-accident_date", "-accident_time", "pk")[: limit + 1]
    )
    truncated = len(rows) > limit
    features = [
        {
            "type": "Feature",
            "geometry": json.loads(r.geometry_json),
            "properties": {
                "id": str(r.pk),
                "reference": r.reference,
                "status": r.status,
                "status_label": r.get_status_display(),
                "type_label": r.get_accident_type_display(),
                "severity": r.severity,
                "severity_label": r.get_severity_display(),
                "date": r.accident_date.isoformat(),
                "time": r.accident_time.strftime("%H:%M"),
                "injured": r.injured_count,
                "deaths": r.death_count,
                "anonymous": r.is_anonymous,
                "in_zone": r.in_zone,
            },
        }
        for r in rows[:limit]
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {"count": len(features), "truncated": truncated},
    }
