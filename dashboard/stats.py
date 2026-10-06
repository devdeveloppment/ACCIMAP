"""
Statistiques du tableau de bord, calculées par PostgreSQL à partir des données réelles.

Règle sur les rejets : un signalement rejeté n'est pas un accident retenu. Les compteurs de
blessés/décès et les graphiques (période, type, gravité) l'excluent donc, SAUF si l'administrateur
filtre explicitement sur le statut « Rejeté ». Les compteurs de signalements (total, par statut,
par mode) comptent toujours tout ce qui correspond aux filtres.
"""
from datetime import timedelta

from django.db.models import Count, Max, Min, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth

from reports.choices import AccidentType, ReportStatus, Severity
from reports.zone import get_coverage_zone

DAILY_MAX_SPAN_DAYS = 62     # au-delà, regroupement par mois
MONTHLY_MAX_POINTS = 60      # on garde les 60 derniers mois


def _series(rows, choices):
    return {
        "codes": [code for code, _ in choices],
        "labels": [label for _, label in choices],
        "values": [rows.get(code, 0) for code, _ in choices],
    }


def _period_series(retained):
    """Nombre d'accidents par jour (période courte) ou par mois (période longue)."""
    bounds = retained.aggregate(first=Min("accident_date"), last=Max("accident_date"))
    first, last = bounds["first"], bounds["last"]
    if first is None:
        return {"granularity": "day", "labels": [], "values": []}

    if (last - first).days <= DAILY_MAX_SPAN_DAYS:
        rows = dict(retained.order_by().values_list("accident_date").annotate(n=Count("pk")))
        labels, values, day = [], [], first
        while day <= last:
            labels.append(day.strftime("%d/%m"))
            values.append(rows.get(day, 0))
            day += timedelta(days=1)
        return {"granularity": "day", "labels": labels, "values": values}

    rows = dict(
        retained.order_by().annotate(month=TruncMonth("accident_date"))
        .values_list("month").annotate(n=Count("pk"))
    )
    months, cursor = [], first.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    months = months[-MONTHLY_MAX_POINTS:]
    return {
        "granularity": "month",
        "labels": [m.strftime("%m/%Y") for m in months],
        "values": [rows.get(m, 0) for m in months],
    }


def compute_stats(queryset, include_rejected=False):
    """Calcule tous les chiffres et séries de graphiques pour `queryset` (déjà filtré)."""
    counts = queryset.aggregate(
        total=Count("pk"),
        anonymous=Count("pk", filter=Q(is_anonymous=True)),
        identified=Count("pk", filter=Q(is_anonymous=False)),
        pending=Count("pk", filter=Q(status=ReportStatus.PENDING)),
        verified=Count("pk", filter=Q(status=ReportStatus.VERIFIED)),
        rejected=Count("pk", filter=Q(status=ReportStatus.REJECTED)),
    )

    retained = queryset if include_rejected else queryset.exclude(status=ReportStatus.REJECTED)
    human = retained.aggregate(
        accidents=Count("pk"),
        vehicles=Coalesce(Sum("vehicle_count"), 0),
        vehicle_reports=Count("vehicle_count"),     # signalements où le nombre de véhicules est renseigné
        injured=Coalesce(Sum("injured_count"), 0),
        deaths=Coalesce(Sum("death_count"), 0),
    )

    by_type = dict(retained.order_by().values_list("accident_type").annotate(n=Count("pk")))
    by_severity = dict(retained.order_by().values_list("severity").annotate(n=Count("pk")))
    outside_zone = queryset.exclude(location__within=get_coverage_zone().geometry).count()

    return {
        "counts": counts,
        "accidents": human["accidents"],
        "vehicles": human["vehicles"],
        "vehicle_reports": human["vehicle_reports"],
        "injured": human["injured"],
        "deaths": human["deaths"],
        "outside_zone": outside_zone,
        "include_rejected": include_rejected,
        "charts": {
            "period": _period_series(retained),
            "types": _series(by_type, AccidentType.choices),
            "severities": _series(by_severity, Severity.choices),
            "statuses": {
                "labels": [label for _, label in ReportStatus.choices],
                "codes": [code for code, _ in ReportStatus.choices],
                "values": [counts["pending"], counts["verified"], counts["rejected"]],
            },
            "modes": {
                "labels": ["Identifiés", "Anonymes"],
                "values": [counts["identified"], counts["anonymous"]],
            },
        },
    }
