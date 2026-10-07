"""Modèle principal : le signalement d'accident."""
import os
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import connection, models
from django.utils import timezone

from .choices import AccidentType, ReportStatus, Severity
from .validators import validate_photo_content, validate_photo_extension, validate_photo_size

# Séquence PostgreSQL (créée par la migration 0002) qui numérote les références.
REFERENCE_SEQUENCE = "reports_reference_seq"


def report_photo_path(instance, filename):
    """
    Chemin de stockage d'une photo : le nom d'origine n'est JAMAIS conservé
    (il peut contenir des informations personnelles ou des caractères dangereux).
    """
    extension = os.path.splitext(filename)[1].lower().lstrip(".")
    if extension not in settings.ALLOWED_IMAGE_EXTENSIONS:
        extension = "bin"
    return f"reports/photos/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{extension}"


class AccidentReportQuerySet(models.QuerySet):
    def public(self):
        """
        Signalements visibles par le public : uniquement « Vérifié ».
        « En attente » et « Rejeté » ne sont JAMAIS publics. C'est l'unique définition de la
        visibilité publique : carte, statistiques et accueil passent tous par ici.
        """
        return self.filter(status=ReportStatus.VERIFIED)


class AccidentReport(models.Model):
    """
    Un accident signalé par un citoyen, avec ou sans identification.

    - La clé primaire est un UUID (non devinable).
    - `reference` (ex. ACC-2026-000123) est un identifiant lisible pour l'affichage.
    - `location` (PointField PostGIS, SRID 4326) est la donnée géographique de
      référence ; `latitude` et `longitude` en sont dérivées automatiquement.
    - Si `is_anonymous` est vrai, aucun utilisateur n'est rattaché au signalement.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(
        "référence", max_length=20, unique=True, editable=False, blank=True
    )

    # --- Déclarant -------------------------------------------------------
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="déclarant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
    )
    is_anonymous = models.BooleanField("signalement anonyme", default=False)

    # --- Accident --------------------------------------------------------
    accident_type = models.CharField(
        "type d'accident", max_length=30, choices=AccidentType.choices
    )
    accident_date = models.DateField("date de l'accident")
    accident_time = models.TimeField("heure de l'accident")
    severity = models.CharField(
        "gravité estimée", max_length=10, choices=Severity.choices, default=Severity.MEDIUM
    )
    vehicle_count = models.PositiveSmallIntegerField(
        "nombre approximatif de véhicules impliqués",
        null=True,
        blank=True,
        validators=[MaxValueValidator(100)],
        help_text="Facultatif. Vide = non renseigné (différent de 0).",
    )
    injured_count = models.PositiveSmallIntegerField(
        "nombre de blessés", default=0, validators=[MaxValueValidator(500)]
    )
    death_count = models.PositiveSmallIntegerField(
        "nombre de décès", default=0, validators=[MaxValueValidator(500)]
    )
    description = models.TextField("description", blank=True, max_length=2000)
    photo = models.ImageField(
        "photographie",
        upload_to=report_photo_path,
        null=True,
        blank=True,
        validators=[validate_photo_extension, validate_photo_size, validate_photo_content],
    )

    # --- Localisation ----------------------------------------------------
    location = gis_models.PointField("localisation", srid=settings.ACCIMAP_SRID)
    latitude = models.FloatField("latitude", null=True, blank=True, editable=False)
    longitude = models.FloatField("longitude", null=True, blank=True, editable=False)

    # --- Suivi administratif --------------------------------------------
    status = models.CharField(
        "statut", max_length=10, choices=ReportStatus.choices, default=ReportStatus.PENDING
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="traité par",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="processed_reports",
    )
    verified_at = models.DateTimeField("date de traitement", null=True, blank=True)
    admin_note = models.TextField("note de l'administrateur", blank=True)

    # --- Métadonnées -----------------------------------------------------
    is_demo = models.BooleanField(
        "donnée de démonstration",
        default=False,
        help_text="Créé par la commande seed_demo : ne correspond pas à un accident réel.",
    )
    created_at = models.DateTimeField("date du signalement", auto_now_add=True)
    updated_at = models.DateTimeField("dernière modification", auto_now=True)

    objects = AccidentReportQuerySet.as_manager()

    class Meta:
        verbose_name = "signalement"
        verbose_name_plural = "signalements"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-accident_date"], name="report_status_date_idx"),
            models.Index(fields=["accident_date"], name="report_date_idx"),
            models.Index(fields=["accident_type"], name="report_type_idx"),
            models.Index(fields=["severity"], name="report_severity_idx"),
            models.Index(fields=["is_anonymous"], name="report_anonymous_idx"),
        ]
        constraints = [
            # Garantie de confidentialité au niveau de la base : un signalement
            # anonyme ne peut JAMAIS être relié à un utilisateur.
            models.CheckConstraint(
                condition=models.Q(is_anonymous=False) | models.Q(user__isnull=True),
                name="report_anonymous_has_no_user",
            ),
            models.CheckConstraint(
                condition=models.Q(latitude__gte=-90, latitude__lte=90)
                & models.Q(longitude__gte=-180, longitude__lte=180)
                | models.Q(latitude__isnull=True),
                name="report_valid_coordinates",
            ),
        ]

    def __str__(self):
        return f"{self.reference or 'Nouveau signalement'} - {self.get_accident_type_display()}"

    # ------------------------------------------------------------------
    # Cohérence des données
    # ------------------------------------------------------------------
    def _sync_coordinates(self):
        """Dérive latitude/longitude de la géométrie PostGIS (source de vérité)."""
        if self.location is None:
            return
        if self.location.srid is None:
            self.location.srid = settings.ACCIMAP_SRID
        elif self.location.srid != settings.ACCIMAP_SRID:
            self.location.transform(settings.ACCIMAP_SRID)
        self.longitude = round(self.location.x, 6)
        self.latitude = round(self.location.y, 6)

    def _next_reference(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT nextval(%s)", [REFERENCE_SEQUENCE])
            number = cursor.fetchone()[0]
        return f"ACC-{timezone.localdate().year}-{number:06d}"

    def clean(self):
        super().clean()
        errors = {}
        self._sync_coordinates()

        if self.latitude is not None and not (
            -90 <= self.latitude <= 90 and -180 <= self.longitude <= 180
        ):
            errors["location"] = "Coordonnées hors limites."

        # Pas d'accident dans le futur (tolérance de 5 min pour l'écart d'horloge).
        if self.accident_date and self.accident_time:
            accident_dt = datetime.combine(self.accident_date, self.accident_time)
            limit = timezone.localtime().replace(tzinfo=None) + timedelta(minutes=5)
            if accident_dt > limit:
                errors["accident_date"] = (
                    "La date et l'heure de l'accident ne peuvent pas être dans le futur."
                )
        elif self.accident_date and self.accident_date > timezone.localdate():
            errors["accident_date"] = "La date de l'accident ne peut pas être dans le futur."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")

        self._sync_coordinates()
        if update_fields is not None and "location" in update_fields:
            kwargs["update_fields"] = {*update_fields, "latitude", "longitude"}

        # Règle de confidentialité : anonyme => aucun utilisateur rattaché.
        if self.is_anonymous:
            self.user = None
            if update_fields is not None and "is_anonymous" in update_fields:
                kwargs["update_fields"] = {*kwargs["update_fields"], "user"}

        if not self.reference:
            self.reference = self._next_reference()
            if update_fields is not None:
                kwargs["update_fields"] = {*kwargs.get("update_fields", update_fields), "reference"}

        super().save(*args, **kwargs)

    def remove_photo(self):
        """Supprime la photo (fichier + référence), par ex. si elle montre des personnes."""
        if self.photo:
            storage, name = self.photo.storage, self.photo.name
            self.photo = None
            self.save(update_fields=["photo", "updated_at"])
            storage.delete(name)

    # ------------------------------------------------------------------
    # Galerie : photo de couverture (self.photo) + photos supplémentaires
    # ------------------------------------------------------------------
    @property
    def gallery(self):
        """Toutes les photos du signalement, couverture d'abord puis les supplémentaires."""
        items = []
        if self.photo:
            items.append({"url_name": "dashboard:report_photo", "args": (self.pk,), "cover": True})
        for extra in self.extra_photos.all():
            items.append({"url_name": "dashboard:report_extra_photo", "args": (self.pk, extra.pk), "cover": False})
        return items

    @property
    def photo_count(self):
        """Nombre total de photos (couverture comprise), sans charger les fichiers."""
        return (1 if self.photo else 0) + self.extra_photos.count()

    # ------------------------------------------------------------------
    # Workflow administratif
    # ------------------------------------------------------------------
    def set_status(self, status, admin_user, note=None):
        """Change le statut et enregistre qui l'a fait et quand."""
        if status not in ReportStatus.values:
            raise ValueError(f"Statut invalide : {status!r}")
        self.status = status
        if status == ReportStatus.PENDING:
            self.verified_by, self.verified_at = None, None
        else:
            self.verified_by, self.verified_at = admin_user, timezone.now()
        fields = ["status", "verified_by", "verified_at", "updated_at"]
        if note is not None:
            self.admin_note = note
            fields.append("admin_note")
        self.save(update_fields=fields)


class ReportPhoto(models.Model):
    """
    Photo supplémentaire rattachée à un signalement (la 1re photo reste sur
    AccidentReport.photo, qui sert de couverture). Même stockage et mêmes
    validations que la couverture ; le contenu est assaini (EXIF retiré) à l'envoi.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        AccidentReport,
        verbose_name="signalement",
        on_delete=models.CASCADE,
        related_name="extra_photos",
    )
    image = models.ImageField(
        "photographie",
        upload_to=report_photo_path,
        validators=[validate_photo_extension, validate_photo_size, validate_photo_content],
    )
    created_at = models.DateTimeField("ajoutée le", auto_now_add=True)

    class Meta:
        verbose_name = "photo de signalement"
        verbose_name_plural = "photos de signalement"
        ordering = ["created_at"]

    def __str__(self):
        return f"Photo de {self.report.reference or 'signalement'}"
