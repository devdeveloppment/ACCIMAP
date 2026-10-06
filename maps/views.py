"""Carte publique : page + API GeoJSON (signalements vérifiés uniquement)."""
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from reports.choices import Severity
from reports.filters import PublicReportFilterForm

from .geojson import public_feature_collection, public_queryset


def public_map(request):
    """Page de la carte. Aucune donnée de signalement n'est incluse dans le HTML :
    tout est chargé dynamiquement depuis l'API."""
    filter_form = PublicReportFilterForm(request.GET or None)
    if not filter_form.is_bound or not filter_form.is_valid():
        filter_form = PublicReportFilterForm(None)  # filtres invalides dans l'URL : on ignore

    context = {
        "filter_form": filter_form,
        "severities": Severity.choices,
        "map_config": {
            "center": list(settings.MAP_DEFAULT_CENTER),
            "zoom": settings.MAP_DEFAULT_ZOOM,
            "tiles": {"url": settings.MAP_TILE_URL, "attribution": settings.MAP_TILE_ATTRIBUTION},
            "dataUrl": reverse("maps:public_data"),
            "showDescription": settings.PUBLIC_MAP_SHOW_DESCRIPTION,
        },
    }
    return render(request, "maps/public_map.html", context)


@require_GET
@never_cache  # une vérification par l'administrateur doit apparaître immédiatement
def public_data(request):
    """GeoJSON des signalements publics (Vérifié), filtrables par période, type, gravité."""
    form = PublicReportFilterForm(request.GET)
    if not form.is_valid():
        return JsonResponse(
            {
                "error": "Filtres invalides.",
                "fields": {name: [str(e) for e in errors] for name, errors in form.errors.items()},
            },
            status=400,
        )
    collection = public_feature_collection(public_queryset(form))
    return JsonResponse(collection, content_type="application/geo+json")
