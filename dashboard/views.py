"""
Espace administrateur (staff) : statistiques, signalements, détail, carte.

Chaque vue exige la permission Django correspondante (voir accounts/roles.py) :
    voir        -> tout ce qui est consultation (tableau de bord, liste, détail, photo, carte)
    modifier    -> changer le statut, ajouter une note, modifier un signalement
    supprimer   -> supprimer un signalement
Aucune de ces vues n'est accessible au public.
"""
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import staff_permission_required
from accounts.roles import PERM_CHANGE, PERM_DELETE, PERM_VIEW
from reports.choices import AccidentType, ReportStatus, Severity
from reports.filters import ReportFilterForm
from reports.models import AccidentReport, ReportPhoto
from reports.zone import annotate_in_zone, get_coverage_zone

from .audit import CHANGE, DELETION, log_action
from .forms import ReportEditForm, ReportReviewForm
from .geojson import admin_feature_collection
from .stats import compute_stats

can_view = staff_permission_required(PERM_VIEW)
can_view_api = staff_permission_required(PERM_VIEW, api=True)
can_change = staff_permission_required(PERM_VIEW, PERM_CHANGE)
can_delete = staff_permission_required(PERM_VIEW, PERM_DELETE)

PAGE_SIZE = 20
SORTS = {
    "-created": ("-created_at",),
    "created": ("created_at",),
    "-accident": ("-accident_date", "-accident_time"),
    "accident": ("accident_date", "accident_time"),
}
SORT_LABELS = [
    ("-created", "Signalés récemment d'abord"),
    ("created", "Signalés anciennement d'abord"),
    ("-accident", "Accident le plus récent"),
    ("accident", "Accident le plus ancien"),
]
PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def filtered_reports(request):
    """Formulaire de filtres + signalements correspondants (vide si les filtres sont invalides)."""
    form = ReportFilterForm(request.GET or None)
    queryset = AccidentReport.objects.all()
    if form.is_bound:
        queryset = form.apply(queryset) if form.is_valid() else queryset.none()
    return form, queryset


def _filters_active(form):
    return form.is_bound and form.is_valid() and any(form.cleaned_data.values())


def _map_base_config():
    return {
        "center": list(settings.MAP_DEFAULT_CENTER),
        "zoom": settings.MAP_DEFAULT_ZOOM,
        "tiles": {"url": settings.MAP_TILE_URL, "attribution": settings.MAP_TILE_ATTRIBUTION},
    }


# ---------------------------------------------------------------------------
# Vue d'ensemble et statistiques
# ---------------------------------------------------------------------------
@can_view
def index(request):
    stats = compute_stats(AccidentReport.objects.all())
    pending = annotate_in_zone(
        AccidentReport.objects.filter(status=ReportStatus.PENDING)
    ).order_by("created_at", "pk")[:5]   # les plus anciens d'abord : file de traitement (pk départage les égalités)
    return render(
        request,
        "dashboard/index.html",
        {"stats": stats, "chart_data": stats["charts"], "pending_reports": pending},
    )


@can_view
def statistics(request):
    form, queryset = filtered_reports(request)
    valid = form.is_bound and form.is_valid()
    include_rejected = valid and form.cleaned_data.get("status") == ReportStatus.REJECTED
    stats = compute_stats(queryset, include_rejected=include_rejected)
    return render(
        request,
        "dashboard/statistics.html",
        {"filter_form": form, "stats": stats, "chart_data": stats["charts"],
         "filters_active": _filters_active(form), "zone": get_coverage_zone()},
    )


# ---------------------------------------------------------------------------
# Liste et détail
# ---------------------------------------------------------------------------
@can_view
def report_list(request):
    form, queryset = filtered_reports(request)
    sort = request.GET.get("sort", "-created")
    if sort not in SORTS:
        sort = "-created"
    queryset = annotate_in_zone(queryset).order_by(*SORTS[sort], "pk")
    page = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "dashboard/report_list.html",
        {"filter_form": form, "page": page, "sort": sort, "sort_labels": SORT_LABELS,
         "querystring": params.urlencode(), "filters_active": _filters_active(form),
         "zone": get_coverage_zone()},
    )


@can_view
def report_detail(request, pk):
    report = get_object_or_404(
        annotate_in_zone(AccidentReport.objects.select_related("user", "verified_by")), pk=pk
    )
    zone = get_coverage_zone()
    map_config = {
        **_map_base_config(),
        "position": [report.location.y, report.location.x],
        "icons": {
            "marker": _static("vendor/leaflet/images/marker-icon.png"),
            "marker2x": _static("vendor/leaflet/images/marker-icon-2x.png"),
            "shadow": _static("vendor/leaflet/images/marker-shadow.png"),
        },
    }
    return render(
        request,
        "dashboard/report_detail.html",
        {
            "report": report,
            "zone": zone,
            "map_config": map_config,
            "review_form": ReportReviewForm(initial={"status": report.status, "admin_note": report.admin_note}),
        },
    )


def _static(path):
    from django.templatetags.static import static
    return static(path)


def _serve_image(image_field):
    """Sert un fichier image (couverture ou photo supplémentaire) en ligne, sans sniffing."""
    if not image_field:
        raise Http404("Pas de photo.")
    try:
        handle = image_field.open("rb")
    except FileNotFoundError:
        raise Http404("Fichier introuvable.")
    extension = Path(image_field.name).suffix.lower()
    response = FileResponse(handle, content_type=PHOTO_TYPES.get(extension, "application/octet-stream"))
    response["Content-Disposition"] = f'inline; filename="photo{extension}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@can_view
def report_photo(request, pk):
    """Sert la photo de couverture UNIQUEMENT aux membres autorisés (jamais d'URL publique /media/)."""
    report = get_object_or_404(AccidentReport, pk=pk)
    return _serve_image(report.photo)


@can_view
def report_extra_photo(request, pk, photo_id):
    """Sert une photo supplémentaire du signalement (espace protégé uniquement)."""
    extra = get_object_or_404(ReportPhoto, pk=photo_id, report_id=pk)
    return _serve_image(extra.image)


# ---------------------------------------------------------------------------
# Actions sur un signalement
# ---------------------------------------------------------------------------
@can_change
@require_POST
def report_review(request, pk):
    """Changement de statut et/ou note. Si le statut ne change pas, seule la note est enregistrée."""
    report = get_object_or_404(AccidentReport, pk=pk)
    form = ReportReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Action invalide : statut ou note incorrect.")
        return redirect("dashboard:report_detail", pk=pk)

    new_status, note = form.cleaned_data["status"], form.cleaned_data["admin_note"]
    previous = report.get_status_display()
    if new_status == report.status:
        report.admin_note = note
        report.save(update_fields=["admin_note", "updated_at"])
        log_action(request.user, report, CHANGE, "Note de l'administrateur modifiée.")
        messages.success(request, "Note enregistrée.")
    else:
        report.set_status(new_status, request.user, note=note)
        log_action(request.user, report, CHANGE, f"Statut : {previous} → {report.get_status_display()}.")
        messages.success(request, f"Statut mis à jour : {report.get_status_display()}.")
    return redirect("dashboard:report_detail", pk=pk)


@can_change
def report_edit(request, pk):
    report = get_object_or_404(AccidentReport, pk=pk)
    form = ReportEditForm(request.POST if request.method == "POST" else None, instance=report)
    if request.method == "POST" and form.is_valid():
        changed = ", ".join(form.changed_data) or "aucun champ"
        form.save()
        if form.cleaned_data.get("remove_photo"):
            report.remove_photo()
        log_action(request.user, report, CHANGE, f"Signalement modifié ({changed}).")
        messages.success(request, "Le signalement a été modifié.")
        return redirect("dashboard:report_detail", pk=pk)
    return render(request, "dashboard/report_edit.html", {"report": report, "form": form})


@can_delete
def report_delete(request, pk):
    """GET : page de confirmation. POST : suppression définitive."""
    report = get_object_or_404(annotate_in_zone(AccidentReport.objects.all()), pk=pk)
    if request.method == "POST":
        reference = report.reference
        log_action(request.user, report, DELETION, "Signalement supprimé.", repr_text=reference)
        report.delete()
        messages.success(request, f"Le signalement {reference} a été supprimé définitivement.")
        return redirect("dashboard:report_list")
    return render(request, "dashboard/report_confirm_delete.html", {"report": report})


# ---------------------------------------------------------------------------
# Carte administrateur : TOUS les statuts
# ---------------------------------------------------------------------------
@can_view
def admin_map(request):
    form = ReportFilterForm(request.GET or None)
    if form.is_bound and not form.is_valid():
        form = ReportFilterForm(None)
    zero = "00000000-0000-0000-0000-000000000000"
    config = {
        **_map_base_config(),
        "admin": True,
        "dataUrl": reverse("dashboard:map_data"),
        "detailUrl": reverse("dashboard:report_detail", args=[zero]).replace(zero, "{id}"),
        "showDescription": False,
    }
    return render(
        request,
        "dashboard/map.html",
        {"filter_form": form, "map_config": config, "severities": Severity.choices,
         "statuses": ReportStatus.choices, "zone": get_coverage_zone()},
    )


@can_view_api
@require_GET
def admin_map_data(request):
    form = ReportFilterForm(request.GET)
    if not form.is_valid():
        return JsonResponse(
            {"error": "Filtres invalides.",
             "fields": {n: [str(e) for e in errs] for n, errs in form.errors.items()}},
            status=400,
        )
    collection = admin_feature_collection(form.apply(AccidentReport.objects.all()))
    return JsonResponse(collection, content_type="application/geo+json")
