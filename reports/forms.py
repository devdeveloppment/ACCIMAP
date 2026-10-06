"""Formulaire de signalement (identique pour les parcours identifié et anonyme)."""
from django import forms
from django.conf import settings
from django.contrib.gis.geos import Point
from django.utils import timezone

from .choices import AccidentType, Severity
from .images import sanitize_photo
from .models import AccidentReport
from .validators import validate_photo_content, validate_photo_extension, validate_photo_size
from .zone import get_coverage_zone

OUTSIDE_ZONE_MESSAGE = (
    "Cette position semble être en dehors de la zone couverte par ACCIMAP. "
    "Cochez la case de confirmation si elle est correcte."
)


class ReportDetailsForm(forms.ModelForm):
    """
    Détails d'un accident (type, date, gravité, nombres, description), avec leur validation.
    Partagé par le formulaire de signalement et par l'édition côté administrateur.
    """

    class Meta:
        model = AccidentReport
        fields = [
            "accident_type",
            "accident_date",
            "accident_time",
            "severity",
            "vehicle_count",
            "injured_count",
            "death_count",
            "description",
        ]
        labels = {
            "accident_type": "Type d'accident",
            "accident_date": "Date",
            "accident_time": "Heure",
            "severity": "Gravité estimée",
            "vehicle_count": "Nombre approximatif de véhicules impliqués",
            "injured_count": "Blessés",
            "death_count": "Décès",
            "description": "Description (facultative)",
        }
        widgets = {
            # <input type="date"> n'accepte QUE le format ISO, quelle que soit la langue du site.
            "accident_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "accident_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "severity": forms.RadioSelect,
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "maxlength": 2000,
                    "placeholder": "Décrivez brièvement ce qui s'est passé (facultatif).",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["accident_type"].choices = [("", "Choisir le type d'accident…")] + list(
            AccidentType.choices
        )
        self.fields["severity"].choices = list(Severity.choices)

        for name, limit in (("vehicle_count", 100), ("injured_count", 500), ("death_count", 500)):
            self.fields[name].widget = forms.NumberInput(
                attrs={"min": 0, "max": limit, "inputmode": "numeric"}
            )
        self.fields["vehicle_count"].required = False   # facultatif : vide = non renseigné
        self.fields["vehicle_count"].widget.attrs["placeholder"] = "—"
        self.fields["accident_date"].widget.attrs["max"] = timezone.localdate().isoformat()

        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.RadioSelect, forms.CheckboxInput)):
                continue
            css = "form-select" if isinstance(widget, forms.Select) else "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class AccidentReportForm(ReportDetailsForm):
    """Formulaire de signalement : détails + localisation + photo facultative."""

    # --- Localisation : champs hors modèle (le modèle en dérive la géométrie PostGIS) ---
    latitude = forms.FloatField(
        label="Latitude",
        widget=forms.TextInput,
        min_value=-90,
        max_value=90,
        error_messages={
            "required": "Veuillez choisir la position de l'accident sur la carte "
            "ou utiliser votre position actuelle.",
            "invalid": "La latitude est invalide.",
            "min_value": "La latitude doit être comprise entre -90 et 90.",
            "max_value": "La latitude doit être comprise entre -90 et 90.",
        },
    )
    longitude = forms.FloatField(
        label="Longitude",
        widget=forms.TextInput,
        min_value=-180,
        max_value=180,
        error_messages={
            "required": "Veuillez choisir la position de l'accident sur la carte "
            "ou utiliser votre position actuelle.",
            "invalid": "La longitude est invalide.",
            "min_value": "La longitude doit être comprise entre -180 et 180.",
            "max_value": "La longitude doit être comprise entre -180 et 180.",
        },
    )
    confirm_outside_zone = forms.BooleanField(
        required=False,
        label="Je confirme que cette position est correcte.",
    )

    class Meta(ReportDetailsForm.Meta):
        fields = ReportDetailsForm.Meta.fields + ["photo"]
        labels = {**ReportDetailsForm.Meta.labels, "photo": "Photographie (facultative)"}
        widgets = {
            **ReportDetailsForm.Meta.widgets,
            "photo": forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outside_zone = False
        # Le type d'accident se choisit par grandes cartes cliquables (rendues dans le gabarit) : pas d'option vide.
        self.fields["accident_type"].choices = list(AccidentType.choices)
        self.fields["accident_type"].widget = forms.RadioSelect()
        self.fields["confirm_outside_zone"].widget.attrs["class"] = "form-check-input"
        for name in ("latitude", "longitude"):
            self.fields[name].widget.attrs.update(
                {"inputmode": "decimal", "placeholder": "—", "autocomplete": "off"}
            )

    # ------------------------------------------------------------------
    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if not photo:
            return None
        # 1) Contrôle du fichier d'origine, 2) ré-encodage sans métadonnées (EXIF...).
        # Fait ICI, donc avant tout stockage de la photo.
        validate_photo_extension(photo)
        validate_photo_size(photo)
        validate_photo_content(photo)
        return sanitize_photo(photo)

    def clean(self):
        cleaned = super().clean()
        latitude, longitude = cleaned.get("latitude"), cleaned.get("longitude")
        if latitude is None or longitude is None:
            return cleaned  # erreurs de champ déjà enregistrées

        # (0, 0) : valeur typique d'un GPS défaillant, jamais un vrai accident à Lomé.
        if latitude == 0 and longitude == 0:
            self.add_error(
                "latitude",
                "La position (0, 0) n'est pas valide. Choisissez le lieu sur la carte.",
            )
            return cleaned

        # Les coordonnées fournies sont conservées TELLES QUELLES.
        self.instance.location = Point(longitude, latitude, srid=settings.ACCIMAP_SRID)

        # Hors zone : simple AVERTISSEMENT, l'utilisateur peut confirmer et poursuivre.
        if not get_coverage_zone().contains(latitude, longitude):
            self.outside_zone = True
            if not cleaned.get("confirm_outside_zone"):
                self.add_error("confirm_outside_zone", OUTSIDE_ZONE_MESSAGE)
        return cleaned
