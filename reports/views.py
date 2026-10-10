"""Parcours public de signalement sans compte ni donnée d'identification."""
from django.conf import settings
from django.core import signing
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.generic import FormView

from .choices import (
    ACCIDENT_CAUSE_ICONS,
    ACCIDENT_TYPE_HINTS,
    ACCIDENT_TYPE_ICONS,
    AccidentCause,
    AccidentType,
)
from .forms import AccidentReportForm
from .geocoding import reverse_geocode
from .models import ReportPhoto
from .zone import get_coverage_zone

SUCCESS_SALT = "reports.success"
SUCCESS_MAX_AGE = 3600  # la page de confirmation reste consultable 1 heure


class ReportCreateView(FormView):
    """
    Le signalement public est toujours anonyme, y compris si le visiteur est déjà
    connecté. Le mode n'est jamais lu depuis le formulaire.
    """

    template_name = "reports/report_form.html"
    form_class = AccidentReportForm
    anonymous = True

    def get_initial(self):
        now = timezone.localtime()
        return {
            "accident_date": now.date(),
            "accident_time": now.strftime("%H:%M"),
            "injured_count": 0,
            "death_count": 0,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        zone = get_coverage_zone()
        form = context["form"]
        context.update(
            type_cards=[
                {"value": value, "label": label, "hint": ACCIDENT_TYPE_HINTS[value], "icon": ACCIDENT_TYPE_ICONS[value]}
                for value, label in AccidentType.choices
            ],
            cause_cards=[
                {"value": value, "label": label, "icon": ACCIDENT_CAUSE_ICONS[value]}
                for value, label in AccidentCause.choices
            ],
            # Les navigateurs vident un champ fichier à chaque rechargement de page : on le dit à l'utilisateur.
            photo_lost=self.request.method == "POST" and form.is_bound and bool(form.errors) and "photos" in self.request.FILES,
            max_photos=settings.MAX_PHOTOS_PER_REPORT,
            vehicle_field=form["vehicle_count"],
            people_fields=[form[name] for name in ("injured_count", "death_count")],
            is_anonymous=self.anonymous,
            zone=zone,
            outside_zone=context["form"].outside_zone,
            max_upload_mb=settings.MAX_UPLOAD_SIZE_MB,
            map_config={
                "center": list(settings.MAP_DEFAULT_CENTER),
                "zoom": settings.MAP_DEFAULT_ZOOM,
                "tiles": {
                    "url": settings.MAP_TILE_URL,
                    "attribution": settings.MAP_TILE_ATTRIBUTION,
                },
                "zone": zone.as_feature(),
                "zoneLabel": zone.label,
                "icons": {
                    "marker": static("vendor/leaflet/images/marker-icon.png"),
                    "marker2x": static("vendor/leaflet/images/marker-icon-2x.png"),
                    "shadow": static("vendor/leaflet/images/marker-shadow.png"),
                },
                "checkUrl": reverse("reports:check_position"),
                "placeUrl": reverse("reports:place"),
                "maxUploadBytes": settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024,
                "maxPhotos": settings.MAX_PHOTOS_PER_REPORT,
            },
        )
        return context

    def form_valid(self, form):
        report = form.save(commit=False)
        report.is_anonymous = True
        report.user = None

        # Photos (déjà validées et assainies par le formulaire) : la 1re sert de
        # couverture (report.photo), les suivantes deviennent des ReportPhoto.
        photos = form.cleaned_data.get("photos") or []
        if photos:
            report.photo = photos[0]
        report.save()
        if len(photos) > 1:
            ReportPhoto.objects.bulk_create(
                [ReportPhoto(report=report, image=image) for image in photos[1:]]
            )

        # Le jeton signé est sans état : aucune identité n'est conservée avec le signalement.
        token = signing.dumps(
            {"ref": report.reference, "anon": report.is_anonymous}, salt=SUCCESS_SALT
        )
        return redirect(f"{reverse('reports:success')}?t={token}")


def report_success(request):
    """Confirmation après enregistrement."""
    try:
        data = signing.loads(
            request.GET.get("t", ""), salt=SUCCESS_SALT, max_age=SUCCESS_MAX_AGE
        )
    except signing.BadSignature:
        return redirect("home")
    return render(
        request,
        "reports/report_success.html",
        {"reference": data["ref"], "is_anonymous": data["anon"]},
    )


@require_GET
def check_position(request):
    """
    Indique si une position est dans la zone de couverture (appelé par la carte du
    formulaire). Un point hors zone n'est PAS une erreur : c'est une information.
    """
    try:
        latitude = float(request.GET["latitude"])
        longitude = float(request.GET["longitude"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "Coordonnées manquantes ou invalides."}, status=400)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return JsonResponse({"error": "Coordonnées hors limites."}, status=400)

    zone = get_coverage_zone()
    return JsonResponse(
        {
            "inside_zone": zone.contains(latitude, longitude),
            "zone_label": zone.label,
            "is_official": zone.is_official,
            "disclaimer": zone.disclaimer,
        }
    )


@require_GET
def place_lookup(request):
    """
    Libellé lisible d'une position (« Quartier, Ville »), pour l'étape « Lieu » du parcours.
    Facultatif : `place` vaut null si le service est indisponible ou désactivé.
    """
    try:
        latitude = float(request.GET["latitude"])
        longitude = float(request.GET["longitude"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "Coordonnées manquantes ou invalides."}, status=400)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return JsonResponse({"error": "Coordonnées hors limites."}, status=400)
    return JsonResponse({"place": reverse_geocode(latitude, longitude)})
