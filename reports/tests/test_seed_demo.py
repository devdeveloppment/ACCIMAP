import re
import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from accounts.roles import ROLE_STAFF, set_role
from reports.choices import AccidentType, ReportStatus, Severity
from reports.management.commands.seed_demo import DEMO_PHONES, HISTORY_DAYS
from reports.models import AccidentReport
from reports.zone import get_coverage_zone

from .helpers import make_report
from .test_zone import SQUARE, write_geojson


def seed(*args, **options):
    out = StringIO()
    call_command("seed_demo", *args, stdout=out, **options)
    return out.getvalue()


def demo():
    return AccidentReport.objects.filter(is_demo=True)


class SeedContentTests(TestCase):
    def setUp(self):
        self.output = seed()
        self.reports = list(demo())

    def test_nombre_et_repartition_des_statuts(self):
        self.assertEqual(len(self.reports), 60)
        counts = {s: sum(1 for r in self.reports if r.status == s) for s in ReportStatus.values}
        self.assertEqual(counts, {"PENDING": 15, "VERIFIED": 36, "REJECTED": 9})

    def test_identifies_et_anonymes(self):
        anonymous = [r for r in self.reports if r.is_anonymous]
        identified = [r for r in self.reports if not r.is_anonymous]
        self.assertEqual((len(anonymous), len(identified)), (24, 36))
        self.assertTrue(all(r.user is None for r in anonymous))
        self.assertTrue(all(r.user and r.user.phone_number in DEMO_PHONES for r in identified))

    def test_tous_les_types_et_toutes_les_gravites(self):
        self.assertEqual({r.accident_type for r in self.reports}, set(AccidentType.values))
        self.assertEqual({r.severity for r in self.reports}, set(Severity.values))

    def test_blesses_et_deces_coherents_avec_la_gravite(self):
        self.assertTrue(any(r.injured_count > 0 for r in self.reports))
        self.assertTrue(any(r.death_count > 0 for r in self.reports))
        for r in self.reports:
            if r.severity == Severity.LOW:
                self.assertEqual(r.death_count, 0)
                self.assertLessEqual(r.injured_count, 1)
            if r.severity == Severity.MEDIUM:
                self.assertEqual(r.death_count, 0)
            if r.severity == Severity.CRITICAL:
                self.assertGreaterEqual(r.death_count, 1)

    def test_vehicules_facultatifs_mix_de_valeurs_et_de_non_renseignes(self):
        values = [r.vehicle_count for r in self.reports]
        self.assertTrue(any(v is None for v in values))
        self.assertTrue(any(v is not None for v in values))
        self.assertTrue(all(v is None or 1 <= v <= 6 for v in values))

    def test_dates_variees_jamais_dans_le_futur(self):
        today = timezone.localdate()
        dates = {r.accident_date for r in self.reports}
        self.assertGreater(len(dates), 30)
        from datetime import timedelta
        self.assertTrue(all(today - timedelta(days=HISTORY_DAYS) <= d < today for d in dates))
        now = timezone.now()
        for r in AccidentReport.objects.filter(is_demo=True):
            self.assertLessEqual(r.created_at, now)
            if r.verified_at:
                self.assertGreaterEqual(r.verified_at, r.created_at)
                self.assertLessEqual(r.verified_at, now)

    def test_traitement_coherent_avec_le_statut(self):
        for r in self.reports:
            if r.status == ReportStatus.PENDING:
                self.assertIsNone(r.verified_at)
                self.assertEqual(r.admin_note, "")
            else:
                self.assertIsNotNone(r.verified_at)
                self.assertIn("démonstration", r.admin_note)

    def test_jamais_de_photo_ni_de_donnee_personnelle(self):
        for r in self.reports:
            self.assertFalse(r.photo)
            self.assertTrue(r.description == "" or r.description.startswith("[DÉMO] "))
            self.assertIsNone(re.search(r"\d{6,}|@|\+228", r.description))

    def test_donnees_clairement_identifiees_comme_demonstration(self):
        self.assertTrue(all(r.is_demo for r in self.reports))
        self.assertIn("marqués « donnée de démonstration »", self.output)
        self.assertIn("Aucune photo, aucune donnée personnelle", self.output)

    def test_comptes_fictifs_reserves(self):
        users = User.objects.all()
        self.assertEqual({u.phone_number for u in users}, set(DEMO_PHONES))
        self.assertTrue(all(not u.is_staff and not u.has_usable_password() for u in users))   # connexion par OTP uniquement

    def test_references_uniques_et_formatees(self):
        references = [r.reference for r in self.reports]
        self.assertEqual(len(set(references)), 60)
        self.assertTrue(all(re.fullmatch(r"ACC-\d{4}-\d{6}", ref) for ref in references))


class SeedGeographyTests(TestCase):
    def test_tous_les_points_dans_la_zone_de_couverture(self):
        seed()
        zone = get_coverage_zone()
        for r in demo():
            self.assertTrue(zone.contains(r.location.y, r.location.x), r.reference)
            self.assertEqual(r.location.srid, 4326)

    def test_latitude_longitude_derivees_du_pointfield(self):
        seed()
        for r in demo():
            self.assertAlmostEqual(r.latitude, r.location.y, places=5)
            self.assertAlmostEqual(r.longitude, r.location.x, places=5)

    def test_filtre_spatial_postgis_sur_les_donnees(self):
        seed()
        inside = AccidentReport.objects.filter(location__within=get_coverage_zone().geometry).count()
        self.assertEqual(inside, 60)
        self.assertEqual(AccidentReport.objects.exclude(location__within=get_coverage_zone().geometry).count(), 0)

    def test_respecte_un_polygone_officiel_configure_plus_tard(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(
            COVERAGE_ZONE_GEOJSON=write_geojson(tmp, {"type": "Polygon", "coordinates": SQUARE}), COVERAGE_ZONE_IS_OFFICIAL=True
        ):
            seed()
            zone = get_coverage_zone()
            self.assertEqual(demo().count(), 60)
            for r in demo():
                self.assertTrue(zone.contains(r.location.y, r.location.x), r.reference)
                self.assertTrue(1.20 <= r.location.x <= 1.25 and 6.10 <= r.location.y <= 6.15)


class SeedIdempotenceTests(TestCase):
    def test_reexecution_sans_option_ne_cree_aucun_doublon(self):
        seed()
        before = list(demo().values_list("pk", flat=True))
        output = seed()
        self.assertEqual(list(demo().values_list("pk", flat=True)), before)
        self.assertEqual(User.objects.count(), 5)
        self.assertIn("existent déjà", output)
        self.assertIn("rien n'a été créé", output)

    def test_reset_regenere_a_l_identique(self):
        seed()
        first = sorted((r.accident_type, r.severity, str(r.accident_date), r.location.wkt, r.status, r.is_anonymous) for r in demo())
        old_pks = set(demo().values_list("pk", flat=True))
        output = seed("--reset")
        self.assertEqual(demo().count(), 60)
        self.assertFalse(old_pks & set(demo().values_list("pk", flat=True)))      # de nouveaux enregistrements
        again = sorted((r.accident_type, r.severity, str(r.accident_date), r.location.wkt, r.status, r.is_anonymous) for r in demo())
        self.assertEqual(first, again)                                              # mêmes données (graine fixe)
        self.assertIn("Réinitialisation", output)

    def test_graine_differente_donnees_differentes(self):
        seed()
        a = sorted(r.location.wkt for r in demo())
        seed("--reset", seed=7)
        b = sorted(r.location.wkt for r in demo())
        self.assertNotEqual(a, b)

    def test_clear_supprime_uniquement_la_demonstration(self):
        real_user = User.objects.create_user("90123456")
        real = make_report(user=real_user, status="VERIFIED")
        seed()
        output = seed("--clear")
        self.assertEqual(demo().count(), 0)
        self.assertEqual(list(AccidentReport.objects.all()), [real])               # la vraie donnée est intacte
        self.assertEqual(list(User.objects.all()), [real_user])
        self.assertIn("60 signalement(s), 5 compte(s)", output)

    def test_clear_sans_donnees_ne_plante_pas(self):
        self.assertIn("0 signalement(s), 0 compte(s)", seed("--clear"))

    def test_un_compte_staff_portant_un_numero_reserve_n_est_jamais_supprime(self):
        staff = User.objects.create_user(DEMO_PHONES[0])
        set_role(staff, ROLE_STAFF)
        seed("--clear")
        self.assertTrue(User.objects.filter(pk=staff.pk).exists())

    def test_les_vraies_donnees_sont_conservees_apres_un_reset(self):
        real = make_report(status="PENDING")
        seed()
        seed("--reset")
        self.assertTrue(AccidentReport.objects.filter(pk=real.pk).exists())
        self.assertEqual(AccidentReport.objects.count(), 61)

    def test_le_verificateur_est_le_staff_existant(self):
        staff = User.objects.create_user("90000099")
        set_role(staff, ROLE_STAFF)
        seed()
        processed = demo().exclude(status="PENDING")
        self.assertTrue(all(r.verified_by == staff for r in processed))


class SeedOptionsTests(TestCase):
    def test_nombre_personnalise(self):
        seed("--count", "12")
        self.assertEqual(demo().count(), 12)

    def test_un_seul_signalement(self):
        seed(count=1)
        self.assertEqual(demo().count(), 1)

    def test_options_invalides(self):
        for kwargs in ({"count": 0}, {"count": -3}, {"count": 2001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(CommandError):
                seed(**kwargs)
        with self.assertRaises(CommandError):
            seed("--reset", "--clear")
        self.assertEqual(demo().count(), 0)

    def test_repartition_proportionnelle_pour_un_autre_effectif(self):
        seed(count=100)
        counts = {s: demo().filter(status=s).count() for s in ReportStatus.values}
        self.assertEqual(counts, {"VERIFIED": 60, "REJECTED": 15, "PENDING": 25})
        self.assertEqual(demo().filter(is_anonymous=True).count(), 40)
