"""
Listes de choix du signalement : un seul endroit à modifier.

Pour ajouter / renommer / retirer un type ou une gravité : éditer la classe
ci-dessous, puis lancer `python manage.py makemigrations` et `migrate`.
Le formulaire, les filtres, les statistiques, la carte et les exports
reprennent automatiquement ces listes.
"""
from django.db import models


class AccidentType(models.TextChoices):
    """Nature de l'accident. Le véhicule ou l'usager concerné n'est volontairement PAS mélangé ici."""

    COLLISION = "COLLISION", "Collision"
    ROLLOVER = "ROLLOVER", "Renversement"
    RUN_OFF_ROAD = "RUN_OFF_ROAD", "Sortie de voie"
    LOSS_OF_CONTROL = "LOSS_OF_CONTROL", "Perte de contrôle"
    INTERSECTION = "INTERSECTION", "Accident à une intersection"
    PILEUP = "PILEUP", "Carambolage / multi-collision"
    PEDESTRIAN = "PEDESTRIAN", "Accident impliquant un piéton"
    OTHER = "OTHER", "Autre"


# Présentation du choix dans le parcours de signalement : phrase d'aide et icône (Lucide, voir
# static/vendor/lucide/). Modifier ici suffit : la page de choix du type utilise ces deux tables.
ACCIDENT_TYPE_HINTS = {
    AccidentType.COLLISION: "Choc entre deux véhicules ou plus",
    AccidentType.ROLLOVER: "Véhicule renversé ou couché sur le côté",
    AccidentType.RUN_OFF_ROAD: "Le véhicule quitte la chaussée",
    AccidentType.LOSS_OF_CONTROL: "Dérapage, véhicule incontrôlable",
    AccidentType.INTERSECTION: "Carrefour, croisement, rond-point",
    AccidentType.PILEUP: "Plusieurs véhicules enchaînés",
    AccidentType.PEDESTRIAN: "Un piéton est touché",
    AccidentType.OTHER: "Une autre situation",
}
ACCIDENT_TYPE_ICONS = {
    AccidentType.COLLISION: "arrow-right-left",
    AccidentType.ROLLOVER: "rotate-ccw",
    AccidentType.RUN_OFF_ROAD: "split",
    AccidentType.LOSS_OF_CONTROL: "tornado",
    AccidentType.INTERSECTION: "signpost",
    AccidentType.PILEUP: "layers",
    AccidentType.PEDESTRIAN: "person-standing",
    AccidentType.OTHER: "ellipsis",
}


class Severity(models.TextChoices):
    LOW = "LOW", "Faible"
    MEDIUM = "MEDIUM", "Moyenne"
    SEVERE = "SEVERE", "Grave"
    CRITICAL = "CRITICAL", "Très grave"


class ReportStatus(models.TextChoices):
    PENDING = "PENDING", "En attente"
    VERIFIED = "VERIFIED", "Vérifié"
    REJECTED = "REJECTED", "Rejeté"
