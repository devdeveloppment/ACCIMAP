"""
Données de DÉMONSTRATION pour ACCIMAP (fictives, sans donnée personnelle).

    python manage.py seed_demo              crée les données si elles n'existent pas (sinon : rien, pas de doublon)
    python manage.py seed_demo --reset      supprime les données de démonstration puis les recrée
    python manage.py seed_demo --clear      supprime uniquement les données de démonstration
    python manage.py seed_demo --count 120 --seed 7

GARANTIES
  * Déterministe : même graine (--seed, 2026 par défaut) = mêmes données, donc démonstrations reproductibles.
  * Réexécutable : sans option, si des données de démonstration existent déjà, rien n'est créé.
  * Identifiée : chaque signalement porte is_demo=True (visible dans le tableau de bord, l'admin et les exports) et
    une description préfixée « [DÉMO] ».
  * Isolée : seuls les signalements is_demo=True et les comptes de démonstration (numéros fictifs +2280000000x,
    jamais attribués) sont créés ou supprimés ; les vraies données ne sont jamais touchées.
  * Sans donnée personnelle : aucun vrai numéro, aucune photo, descriptions génériques.
  * Dans la zone de couverture ACTIVE (rectangle indicatif, ou polygone officiel si configuré).
"""
import random
from datetime import datetime, time, timedelta

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from reports.choices import AccidentType, ReportStatus, Severity
from reports.models import AccidentReport
from reports.zone import get_coverage_zone

# Numéros FICTIFS réservés à la démonstration (aucun numéro togolais réel ne commence par « 00 »).
DEMO_PHONES = [f"+2280000000{i}" for i in range(1, 6)]

# Secteurs FICTIFS de forte activité : centres approximatifs autour de Lomé, sans valeur géographique officielle.
# (libellé, longitude, latitude, poids)
HOTSPOTS = [
    ("centre", 1.2228, 6.1319, 30),
    ("nord", 1.2150, 6.1900, 18),
    ("ouest", 1.1950, 6.1700, 17),
    ("est", 1.3000, 6.1350, 15),
    ("aeroport", 1.2545, 6.1656, 12),
    ("peripherie nord", 1.2250, 6.2450, 8),
]
SIGMA_DEGREES = 0.006      # dispersion autour du centre (≈ 650 m)
HISTORY_DAYS = 180

TYPE_WEIGHTS = {
    AccidentType.COLLISION: 28, AccidentType.INTERSECTION: 17, AccidentType.PEDESTRIAN: 14,
    AccidentType.LOSS_OF_CONTROL: 12, AccidentType.RUN_OFF_ROAD: 10, AccidentType.ROLLOVER: 7,
    AccidentType.PILEUP: 5, AccidentType.OTHER: 7,
}
# Gravité : poids (faible, moyenne, grave, très grave) selon le type.
SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.SEVERE, Severity.CRITICAL]
SEVERITY_WEIGHTS = {
    "default": (30, 40, 22, 8),
    AccidentType.PEDESTRIAN: (10, 35, 40, 15),
    AccidentType.PILEUP: (5, 30, 45, 20),
    AccidentType.ROLLOVER: (15, 35, 35, 15),
}
# (blessés min, max), (décès min, max, probabilité) selon la gravité.
CASUALTIES = {
    Severity.LOW: ((0, 1), (0, 0, 0)),
    Severity.MEDIUM: ((0, 3), (0, 0, 0)),
    Severity.SEVERE: ((1, 5), (0, 1, 0.2)),
    Severity.CRITICAL: ((2, 8), (1, 3, 1.0)),
}
VEHICLES = {  # (min, max) de véhicules selon le type ; ~22 % de « non renseigné » (champ facultatif)
    AccidentType.COLLISION: (2, 3), AccidentType.INTERSECTION: (2, 3), AccidentType.PEDESTRIAN: (1, 1),
    AccidentType.LOSS_OF_CONTROL: (1, 1), AccidentType.RUN_OFF_ROAD: (1, 1), AccidentType.ROLLOVER: (1, 1),
    AccidentType.PILEUP: (3, 6), AccidentType.OTHER: (1, 2),
}
HOUR_WEIGHTS = [1, 1, 1, 1, 1, 2, 4, 9, 10, 6, 4, 4, 5, 5, 4, 5, 7, 10, 11, 9, 6, 4, 3, 2]
DESCRIPTIONS = {
    AccidentType.COLLISION: ["Choc entre deux véhicules, circulation ralentie.", "Collision à vitesse modérée, dégâts matériels."],
    AccidentType.INTERSECTION: ["Accident à un carrefour très fréquenté.", "Priorité non respectée à un croisement."],
    AccidentType.PEDESTRIAN: ["Un piéton a été touché en traversant.", "Accident près d'un passage piéton."],
    AccidentType.LOSS_OF_CONTROL: ["Véhicule parti en dérapage sur chaussée mouillée."],
    AccidentType.RUN_OFF_ROAD: ["Le véhicule a quitté la chaussée."],
    AccidentType.ROLLOVER: ["Véhicule renversé sur le côté de la route."],
    AccidentType.PILEUP: ["Plusieurs véhicules impliqués à la suite d'un freinage brusque."],
    AccidentType.OTHER: ["Situation inhabituelle signalée sur la voie."],
}
REJECTION_NOTES = ["Doublon d'un autre signalement", "Position incohérente avec la description", "Informations insuffisantes"]


def _weighted(rng, mapping):
    keys = list(mapping)
    return rng.choices(keys, weights=[mapping[k] for k in keys])[0]


class Command(BaseCommand):
    help = "Crée (ou supprime) des données de démonstration fictives, sans doublon."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Supprime les données de démonstration puis les recrée.")
        parser.add_argument("--clear", action="store_true", help="Supprime uniquement les données de démonstration.")
        parser.add_argument("--count", type=int, default=60, help="Nombre de signalements (1 à 2000, défaut 60).")
        parser.add_argument("--seed", type=int, default=2026, help="Graine aléatoire (mêmes données pour une même graine).")

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        if options["reset"] and options["clear"]:
            raise CommandError("Choisissez --reset OU --clear, pas les deux.")
        count = options["count"]
        if not 1 <= count <= 2000:
            raise CommandError("--count doit être compris entre 1 et 2000.")

        if options["clear"]:
            reports, users = self._delete_demo()
            self.stdout.write(self.style.SUCCESS(f"Données de démonstration supprimées : {reports} signalement(s), {users} compte(s)."))
            return

        if options["reset"]:
            reports, users = self._delete_demo()
            self.stdout.write(f"Réinitialisation : {reports} signalement(s) et {users} compte(s) de démonstration supprimés.")

        existing = AccidentReport.objects.filter(is_demo=True).count()
        if existing:
            self.stdout.write(self.style.WARNING(
                f"Des données de démonstration existent déjà ({existing} signalements) : rien n'a été créé. "
                "Utilisez --reset pour les régénérer ou --clear pour les supprimer."))
            return

        with transaction.atomic():
            summary = self._create(count, options["seed"])
        self._print_summary(summary)

    # ------------------------------------------------------------------
    def _delete_demo(self):
        with transaction.atomic():
            reports = AccidentReport.objects.filter(is_demo=True)
            n_reports = reports.count()
            reports.delete()
            # Jamais un compte administrateur, même s'il portait un numéro réservé.
            users = User.objects.filter(phone_number__in=DEMO_PHONES, is_staff=False, is_superuser=False)
            n_users = users.count()
            users.delete()
        return n_reports, n_users

    def _random_point(self, rng, zone):
        weights = [h[3] for h in HOTSPOTS]
        for _ in range(30):
            _, lon, lat, _w = rng.choices(HOTSPOTS, weights=weights)[0]
            lon, lat = rng.gauss(lon, SIGMA_DEGREES), rng.gauss(lat, SIGMA_DEGREES)
            if zone.contains(lat, lon):
                return round(lon, 6), round(lat, 6)
        # Zone personnalisée (polygone officiel) ne contenant pas les secteurs : tirage dans son emprise.
        min_x, min_y, max_x, max_y = zone.geometry.extent
        for _ in range(2000):
            lon, lat = rng.uniform(min_x, max_x), rng.uniform(min_y, max_y)
            if zone.contains(lat, lon):
                return round(lon, 6), round(lat, 6)
        raise CommandError("Impossible de placer un point dans la zone de couverture active.")

    def _create(self, count, seed):
        rng = random.Random(seed)
        zone = get_coverage_zone()
        now = timezone.now()
        today = timezone.localdate()

        n_verified, n_rejected = round(count * 0.60), round(count * 0.15)
        statuses = ([ReportStatus.VERIFIED] * n_verified + [ReportStatus.REJECTED] * n_rejected
                    + [ReportStatus.PENDING] * (count - n_verified - n_rejected))
        n_anonymous = round(count * 0.40)
        anonymous_flags = [True] * n_anonymous + [False] * (count - n_anonymous)
        rng.shuffle(statuses)
        rng.shuffle(anonymous_flags)

        users = [User.objects.create_user(phone) for phone in DEMO_PHONES]
        verifier = User.objects.filter(is_staff=True).order_by("pk").first()    # peut être None : « traité par » vide

        created = []
        for status, anonymous in zip(statuses, anonymous_flags):
            accident_type = _weighted(rng, TYPE_WEIGHTS)
            weights = SEVERITY_WEIGHTS.get(accident_type, SEVERITY_WEIGHTS["default"])
            severity = rng.choices(SEVERITY_ORDER, weights=weights)[0]
            (inj_min, inj_max), (dead_min, dead_max, dead_prob) = CASUALTIES[severity]
            deaths = rng.randint(dead_min, dead_max) if rng.random() < dead_prob else 0
            v_min, v_max = VEHICLES[accident_type]
            vehicles = None if rng.random() < 0.22 else rng.randint(v_min, v_max)

            day = today - timedelta(days=rng.randint(1, HISTORY_DAYS))
            hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
            at = time(hour, rng.choice([0, 10, 15, 20, 30, 40, 45, 50]))
            lon, lat = self._random_point(rng, zone)
            description = ""
            if rng.random() < 0.7:
                description = "[DÉMO] " + rng.choice(DESCRIPTIONS[accident_type])

            report = AccidentReport(
                user=None if anonymous else rng.choice(users),
                is_anonymous=anonymous, accident_type=accident_type, accident_date=day, accident_time=at,
                severity=severity, vehicle_count=vehicles, injured_count=rng.randint(inj_min, inj_max),
                death_count=deaths, description=description, location=Point(lon, lat, srid=4326),
                status=status, is_demo=True,
            )
            if status != ReportStatus.PENDING:
                report.admin_note = ("Vérifié (données de démonstration)." if status == ReportStatus.VERIFIED
                                     else f"{rng.choice(REJECTION_NOTES)} (démonstration).")
            report.save()

            # Dates réalistes : signalé peu après l'accident, traité dans les jours suivants (jamais dans le futur).
            accident_dt = timezone.make_aware(datetime.combine(day, at))
            created_at = min(accident_dt + timedelta(minutes=rng.randint(5, 360)), now - timedelta(minutes=1))
            updates = {"created_at": created_at, "updated_at": created_at}
            if status != ReportStatus.PENDING:
                processed = min(created_at + timedelta(hours=rng.randint(1, 72)), now - timedelta(seconds=30))
                updates.update(verified_at=processed, updated_at=processed, verified_by=verifier)
            AccidentReport.objects.filter(pk=report.pk).update(**updates)
            created.append(report)
        return {"reports": created, "users": len(users), "verifier": verifier is not None}

    def _print_summary(self, summary):
        reports = summary["reports"]
        by = lambda attr: {value: sum(1 for r in reports if getattr(r, attr) == value) for value in sorted({getattr(r, attr) for r in reports})}
        status = by("status")
        self.stdout.write(self.style.SUCCESS(f"Données de démonstration créées : {len(reports)} signalements, {summary['users']} comptes fictifs."))
        self.stdout.write(f"  Statuts   : {status.get('VERIFIED', 0)} vérifiés, {status.get('PENDING', 0)} en attente, {status.get('REJECTED', 0)} rejetés")
        anonymous = sum(1 for r in reports if r.is_anonymous)
        self.stdout.write(f"  Modes     : {len(reports) - anonymous} identifiés, {anonymous} anonymes")
        self.stdout.write(f"  Types     : {len(by('accident_type'))} types différents ; gravités : {len(by('severity'))} niveaux")
        self.stdout.write(f"  Victimes  : {sum(r.injured_count for r in reports)} blessés, {sum(r.death_count for r in reports)} décès (fictifs)")
        dates = sorted(r.accident_date for r in reports)
        self.stdout.write(f"  Période   : du {dates[0]:%d/%m/%Y} au {dates[-1]:%d/%m/%Y}")
        self.stdout.write("  Tous les signalements sont marqués « donnée de démonstration » (is_demo). Aucune photo, aucune donnée personnelle.")
        if not summary["verifier"]:
            self.stdout.write("  Aucun compte staff n'existe encore : « traité par » reste vide. Créez-en un avec : python manage.py createsuperuser")
        self.stdout.write("  Pour supprimer ces données : python manage.py seed_demo --clear")
