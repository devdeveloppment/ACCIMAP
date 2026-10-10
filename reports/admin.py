"""
Django Admin : deuxième niveau d'administration (secours). Mêmes permissions que le tableau de bord.

CONFIDENTIALITÉ : jamais de numéro de téléphone ici. Les relations vers l'utilisateur (déclarant,
personne ayant traité) ne sont PAS des champs du formulaire : seules des valeurs masquées sont affichées.
Photo : jamais d'URL /media/ ; seulement un lien vers la vue protégée du tableau de bord.
"""
from django.contrib import admin, messages
from django.contrib.gis.admin import GISModelAdmin
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from accounts.phone import mask_phone
from dashboard.audit import CHANGE, log_action, log_export
from reports.choices import ReportStatus
from reports.models import AccidentReport, ReportPhoto
from reports.zone import annotate_in_zone, get_coverage_zone


class ReportPhotoInline(admin.TabularInline):
    """Photos supplémentaires du signalement (la couverture reste sur le champ photo)."""

    model = ReportPhoto
    extra = 0
    can_delete = True
    fields = ("thumbnail", "created_at")
    readonly_fields = ("thumbnail", "created_at")

    def has_add_permission(self, request, obj=None):
        return False  # l'ajout se fait par le formulaire citoyen, pas ici

    @admin.display(description="Aperçu")
    def thumbnail(self, obj):
        if not obj.image:
            return "—"
        url = reverse("dashboard:report_extra_photo", args=[obj.report_id, obj.pk])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Ouvrir (espace protégé)</a><br>'
            '<img src="{}" alt="Photo supplémentaire" style="max-width:220px;max-height:160px;margin-top:6px">',
            url, url,
        )


class ZoneFilter(admin.SimpleListFilter):
    """Dans / hors de la zone de couverture, calculé à la volée par PostGIS."""

    title = "zone de couverture"
    parameter_name = "zone"

    def lookups(self, request, model_admin):
        return [("inside", "Dans la zone"), ("outside", "Hors zone")]

    def queryset(self, request, queryset):
        if self.value() not in ("inside", "outside"):
            return queryset
        geometry = get_coverage_zone().geometry
        inside = queryset.filter(location__within=geometry)
        return inside if self.value() == "inside" else queryset.exclude(pk__in=inside.values("pk"))


@admin.register(AccidentReport)
class AccidentReportAdmin(GISModelAdmin):
    gis_widget_kwargs = {"attrs": {"default_lon": 1.2314, "default_lat": 6.1725, "default_zoom": 12}}
    object_history_template = "admin/reports/accidentreport/object_history.html"

    list_display = ("reference", "reported_type_display", "accident_date", "severity", "status", "mode_display",
                    "injured_count", "death_count", "zone_display", "photo_flag", "created_at")
    list_filter = ("status", "accident_type", "severity", "is_anonymous", "is_demo",
                   ("accident_date", admin.DateFieldListFilter), ZoneFilter, ("created_at", admin.DateFieldListFilter))
    search_fields = ("reference", "description", "admin_note")
    date_hierarchy = "accident_date"
    ordering = ("-created_at",)
    list_per_page = 25
    actions = ["mark_verified", "mark_rejected", "mark_pending", "export_selected_csv"]
    inlines = [ReportPhotoInline]

    fieldsets = (
        ("Identification", {"fields": ("reference", "status", "mode_display", "declarant_display", "is_demo")}),
        ("Accident", {"fields": ("accident_type", "accident_cause", "accident_date", "accident_time", "severity", "vehicle_count",
                                 "injured_count", "death_count", "description")}),
        ("Localisation", {"fields": ("location", "latitude", "longitude", "zone_display")}),
        ("Photographie", {"fields": ("photo_display",)}),
        ("Suivi administratif", {"fields": ("admin_note", "processed_by_display", "verified_at", "created_at", "updated_at")}),
    )
    readonly_fields = ("reference", "mode_display", "declarant_display", "is_demo", "latitude", "longitude",
                       "zone_display", "photo_display", "processed_by_display", "verified_at", "created_at", "updated_at")

    # ---- Données ----------------------------------------------------------------------------
    def get_queryset(self, request):
        return annotate_in_zone(super().get_queryset(request))

    @admin.display(description="Type d'accident", ordering="accident_type")
    def reported_type_display(self, obj):
        return obj.get_reported_type_display()

    def has_add_permission(self, request):
        return False   # les signalements viennent des citoyens ; l'administration ne les fabrique pas

    # ---- Colonnes et champs calculés ----------------------------------------------------------
    @admin.display(description="Mode", ordering="is_anonymous")
    def mode_display(self, obj):
        return "Anonyme" if obj.is_anonymous else "Identifié"

    @admin.display(description="Déclarant")
    def declarant_display(self, obj):
        if obj.is_anonymous:
            return "Anonyme (aucune identité enregistrée)"
        return f"Identifié ({mask_phone(obj.user.phone_number)})" if obj.user_id else "Identifié (compte supprimé)"

    @admin.display(description="Traité par")
    def processed_by_display(self, obj):
        return mask_phone(obj.verified_by.phone_number) if obj.verified_by_id else "—"

    @admin.display(boolean=True, description="Dans la zone")
    def zone_display(self, obj):
        return getattr(obj, "in_zone", None)

    @admin.display(boolean=True, description="Photo")
    def photo_flag(self, obj):
        return bool(obj.photo)

    @admin.display(description="Photographie")
    def photo_display(self, obj):
        if not obj.photo:
            return "Aucune photo."
        url = reverse("dashboard:report_photo", args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Ouvrir la photo (espace protégé)</a><br>'
            '<img src="{}" alt="Photo du signalement" style="max-width:320px;max-height:240px;margin-top:6px">',
            url, url,
        )

    # ---- Enregistrement : le workflow de statut reste cohérent ---------------------------------
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and "status" in form.changed_data:
            obj.set_status(obj.status, request.user)   # enregistre qui a traité et quand

    # ---- Actions -----------------------------------------------------------------------------
    def _bulk_status(self, request, queryset, status):
        changed = 0
        for report in queryset:
            if report.status == status:
                continue
            previous = report.get_status_display()
            report.set_status(status, request.user)
            log_action(request.user, report, CHANGE, f"Statut : {previous} → {report.get_status_display()} (action groupée).")
            changed += 1
        self.message_user(request, f"{changed} signalement(s) mis à jour.", messages.SUCCESS)

    @admin.action(description="Marquer comme vérifié", permissions=["change"])
    def mark_verified(self, request, queryset):
        self._bulk_status(request, queryset, ReportStatus.VERIFIED)

    @admin.action(description="Marquer comme rejeté", permissions=["change"])
    def mark_rejected(self, request, queryset):
        self._bulk_status(request, queryset, ReportStatus.REJECTED)

    @admin.action(description="Remettre en attente", permissions=["change"])
    def mark_pending(self, request, queryset):
        self._bulk_status(request, queryset, ReportStatus.PENDING)

    @admin.action(description="Exporter la sélection en CSV (sans numéro de téléphone)", permissions=["view"])
    def export_selected_csv(self, request, queryset):
        from exports import services

        fresh = AccidentReport.objects.filter(pk__in=queryset.values_list("pk", flat=True))
        content = services.build_csv(services.export_queryset(fresh).iterator())
        log_export(request.user, "CSV (admin)", fresh.count(), "sélection manuelle")
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="accimap_selection_{timezone.localdate():%Y-%m-%d}.csv"'
        return response
