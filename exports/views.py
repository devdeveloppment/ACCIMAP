"""
Exports CSV / Excel / PDF : réservés au staff autorisé (permission « voir »), jamais publics.

Les exports reprennent EXACTEMENT les filtres du tableau de bord (mêmes paramètres d'URL) et ne
contiennent aucun numéro de téléphone (voir exports/services.py). Chaque téléchargement est journalisé.
"""
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.decorators import staff_permission_required
from accounts.roles import PERM_VIEW
from dashboard.audit import log_export
from dashboard.stats import compute_stats
from dashboard.views import filtered_reports
from reports.choices import ReportStatus

from . import services

can_export = staff_permission_required(PERM_VIEW)


def _index_url(request):
    query = request.GET.urlencode()
    return f"{reverse('exports:index')}?{query}" if query else reverse("exports:index")


def _include_rejected(form):
    return form.is_bound and form.is_valid() and form.cleaned_data.get("status") == ReportStatus.REJECTED


@can_export
def index(request):
    form, queryset = filtered_reports(request)
    count = queryset.count()
    return render(
        request,
        "exports/index.html",
        {
            "filter_form": form,
            "count": count,
            "filters": services.describe_filters(form),
            "querystring": request.GET.urlencode(),
            "max_rows": settings.EXPORT_MAX_ROWS,
            "pdf_max_rows": settings.EXPORT_PDF_MAX_ROWS,
            "too_many": count > settings.EXPORT_MAX_ROWS,
            "pdf_truncated": count > settings.EXPORT_PDF_MAX_ROWS,
        },
    )


def _prepare(request):
    """Filtres valides et au moins un résultat ; sinon redirection explicative vers la page d'export."""
    form, queryset = filtered_reports(request)
    if form.is_bound and not form.is_valid():
        messages.error(request, "Filtres invalides : corrigez-les avant d'exporter.")
        return None, redirect(_index_url(request))
    count = queryset.count()
    if count == 0:
        messages.warning(request, "Aucun signalement ne correspond aux filtres : rien à exporter.")
        return None, redirect(_index_url(request))
    return (form, queryset, count), None


def _attachment(content, content_type, extension):
    filename = f"accimap_signalements_{timezone.localdate():%Y-%m-%d}.{extension}"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _refuse_too_many(request, count):
    messages.error(
        request,
        f"{count} signalements : trop pour ce format (maximum {settings.EXPORT_MAX_ROWS}). Affinez les filtres.",
    )
    return redirect(_index_url(request))


@can_export
@require_GET
def export_csv(request):
    prepared, response = _prepare(request)
    if response:
        return response
    form, queryset, count = prepared
    if count > settings.EXPORT_MAX_ROWS:
        return _refuse_too_many(request, count)
    content = services.build_csv(services.export_queryset(queryset).iterator())
    log_export(request.user, "CSV", count, services.filters_summary_text(form))
    return _attachment(content, "text/csv; charset=utf-8", "csv")


@can_export
@require_GET
def export_xlsx(request):
    prepared, response = _prepare(request)
    if response:
        return response
    form, queryset, count = prepared
    if count > settings.EXPORT_MAX_ROWS:
        return _refuse_too_many(request, count)
    stats = compute_stats(queryset, include_rejected=_include_rejected(form))
    content = services.build_xlsx(services.export_queryset(queryset).iterator(), form, stats)
    log_export(request.user, "Excel", count, services.filters_summary_text(form))
    return _attachment(
        content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    )


@can_export
@require_GET
def export_pdf(request):
    prepared, response = _prepare(request)
    if response:
        return response
    form, queryset, count = prepared
    limit = settings.EXPORT_PDF_MAX_ROWS
    stats = compute_stats(queryset, include_rejected=_include_rejected(form))
    reports = list(services.export_queryset(queryset)[:limit])
    content = services.build_pdf(reports, form, stats, count, truncated_at=limit if count > limit else None)
    log_export(request.user, "PDF", min(count, limit), services.filters_summary_text(form))
    return _attachment(content, "application/pdf", "pdf")
