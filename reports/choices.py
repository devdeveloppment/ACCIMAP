"""
Listes de choix du signalement : un seul endroit à modifier.

Pour ajouter / renommer / retirer un type, une cause ou une gravité : éditer la classe
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


class AccidentCause(models.TextChoices):
    """Facteurs ayant contribué à l'accident, distincts de sa nature."""

    EXCESSIVE_SPEED = "EXCESSIVE_SPEED", "Vitesse excessive"
    RULE_VIOLATION = "RULE_VIOLATION", "Non-respect des règles"
    IMPAIRED_DRIVING = (
        "IMPAIRED_DRIVING",
        "Conduite sous l'emprise des stupéfiants et de l'alcool",
    )
    REDUCED_VISIBILITY = "REDUCED_VISIBILITY", "Visibilité dégradée"
    MECHANICAL_FAILURE = "MECHANICAL_FAILURE", "Défaillance mécanique"
    WEATHER_AND_ROAD_CONDITIONS = (
        "WEATHER_AND_ROAD_CONDITIONS",
        "Mauvaises conditions météorologiques et état de la chaussée",
    )


ACCIDENT_CAUSE_ICONS = {
    AccidentCause.EXCESSIVE_SPEED: "siren",
    AccidentCause.RULE_VIOLATION: "shield-check",
    AccidentCause.IMPAIRED_DRIVING: "triangle-alert",
    AccidentCause.REDUCED_VISIBILITY: "eye",
    AccidentCause.MECHANICAL_FAILURE: "car-front",
    AccidentCause.WEATHER_AND_ROAD_CONDITIONS: "map-pin",
}


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
    AccidentType.OTHER: "Choisissez une précision",
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

ACCIDENT_TYPE_COLORS = {
    AccidentType.COLLISION: "#2563eb",
    AccidentType.ROLLOVER: "#7c3aed",
    AccidentType.RUN_OFF_ROAD: "#0f766e",
    AccidentType.LOSS_OF_CONTROL: "#d97706",
    AccidentType.INTERSECTION: "#be185d",
    AccidentType.PILEUP: "#0e7490",
    AccidentType.PEDESTRIAN: "#4338ca",
    AccidentType.OTHER: "#475569",
}


def accident_type_color(code):
    """Retourne la couleur stable d'un type, avec une teinte déterministe pour les nouveaux codes."""
    if code in ACCIDENT_TYPE_COLORS:
        return ACCIDENT_TYPE_COLORS[code]

    value = 0
    for character in str(code):
        value = (value * 31 + ord(character)) & 0xFFFFFFFF
    hue = value % 360
    return f"hsl({hue} 62% 42%)"


class Severity(models.TextChoices):
    LOW = "LOW", "Faible"
    MEDIUM = "MEDIUM", "Moyenne"
    SEVERE = "SEVERE", "Grave"
    CRITICAL = "CRITICAL", "Très grave"


class ReportStatus(models.TextChoices):
    PENDING = "PENDING", "En attente"
    VERIFIED = "VERIFIED", "Vérifié"
    REJECTED = "REJECTED", "Rejeté"
