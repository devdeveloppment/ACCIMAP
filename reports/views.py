"""Parcours de signalement : identifié (connexion requise) et anonyme."""
from django.conf import settings
from django.core import signing
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.generic import FormView

from .choices import ACCIDENT_TYPE_HINTS, ACCIDENT_TYPE_ICONS, AccidentType
from .forms import AccidentReportForm
from .geocoding import reverse_geocode
from .zone import get_coverage_zone

SUCCESS_SALT = "reports.success"
SUCCESS_MAX_AGE = 3600  # la page de confirmation reste consultable 1 heure


class ReportCreateView(FormView):
    """
    Même formulaire pour les deux parcours ; seul `anonymous` change :

    * anonymous=False : réservé aux utilisateurs connectés (voir urls.py) ; le
      signalement est rattaché à leur compte.
    * anonymous=True  : sans connexion ; AUCUN utilisateur n'est jamais rattaché,
      même si le visiteur est connecté par ailleurs.

    Le mode n'est jamais lu depuis le formulaire (impossible à falsifier côté client).
    """

    template_name = "reports/report_form.html"
    form_class = AccidentReportForm
    anonymous = False

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
            # Les navigateurs vident un champ fichier à chaque rechargement de page : on le dit à l'utilisateur.
            photo_lost=self.request.method == "POST" and form.is_bound and bool(form.errors) and "photo" in self.request.FILES,
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
            },
        )
        return context

    def form_valid(self, form):
        report = form.save(commit=False)
        report.is_anonymous = self.anonymous
        report.user = None if self.anonymous else self.request.user
        report.save()

        # Le jeton signé est sans état : rien n'est écrit en session, donc aucun lien
        # n'est conservé entre un compte connecté et un signalement anonyme.
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
