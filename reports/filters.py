"""
Filtres des signalements : UNE seule implémentation pour la carte publique, la liste
administrateur, les statistiques et les exports.

* ReportFilterForm        : tous les filtres (espace administrateur).
* PublicReportFilterForm  : sous-ensemble sûr pour le public (période, type, gravité).
  Le statut et le mode identifié/anonyme restent réservés à l'administrateur : le public ne
  doit pas pouvoir distinguer les signalements anonymes, ni voir d'autres statuts que « Vérifié ».
"""
from django import forms
from django.db.models import Q

from .choices import AccidentType, ReportStatus, Severity
from .zone import get_coverage_zone

MODE_CHOICES = [("", "Tous les modes"), ("identified", "Identifiés"), ("anonymous", "Anonymes")]
ZONE_CHOICES = [
    ("", "Toutes les positions"),
    ("inside", "Dans la zone de couverture"),
    ("outside", "Hors de la zone de couverture"),
]


class ReportFilterForm(forms.Form):
    date_from = forms.DateField(
        required=False, label="Du", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    date_to = forms.DateField(
        required=False, label="Au", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    accident_type = forms.ChoiceField(
        required=False, label="Type d'accident", choices=[("", "Tous les types")] + AccidentType.choices
    )
    severity = forms.ChoiceField(
        required=False, label="Gravité", choices=[("", "Toutes les gravités")] + Severity.choices
    )
    # --- Réservés à l'administrateur ---
    status = forms.ChoiceField(
        required=False, label="Statut", choices=[("", "Tous les statuts")] + ReportStatus.choices
    )
    mode = forms.ChoiceField(required=False, label="Mode", choices=MODE_CHOICES)
    zone = forms.ChoiceField(required=False, label="Zone", choices=ZONE_CHOICES)

    q = forms.CharField(
        required=False, max_length=100, label="Recherche",
        widget=forms.TextInput(attrs={"placeholder": "Référence, description, note…", "type": "search"}),
    )

    ADMIN_ONLY = ("status", "mode", "zone", "q")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs["class"] = css

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "La date de fin doit être postérieure à la date de début.")
        return cleaned

    def apply(self, queryset):
        """Applique les filtres renseignés (le formulaire doit être valide)."""
        data = self.cleaned_data
        if data.get("date_from"):
            queryset = queryset.filter(accident_date__gte=data["date_from"])
        if data.get("date_to"):
            queryset = queryset.filter(accident_date__lte=data["date_to"])
        if data.get("accident_type"):
            queryset = queryset.filter(accident_type=data["accident_type"])
        if data.get("severity"):
            queryset = queryset.filter(severity=data["severity"])
        if data.get("status"):
            queryset = queryset.filter(status=data["status"])
        if data.get("mode"):
            queryset = queryset.filter(is_anonymous=(data["mode"] == "anonymous"))
        if data.get("q"):
            term = data["q"].strip()
            queryset = queryset.filter(
                Q(reference__icontains=term) | Q(description__icontains=term) | Q(admin_note__icontains=term)
            )
        if data.get("zone"):
            inside = queryset.filter(location__within=get_coverage_zone().geometry)
            queryset = inside if data["zone"] == "inside" else queryset.exclude(pk__in=inside.values("pk"))
        return queryset


class PublicReportFilterForm(ReportFilterForm):
    """Filtres exposés au public : jamais le statut, le mode ni la zone."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.ADMIN_ONLY:
            del self.fields[name]
