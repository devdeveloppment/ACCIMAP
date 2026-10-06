import re
import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.gis.geos import Point, Polygon
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from reports.choices import AccidentType, ReportStatus, Severity
from reports.models import AccidentReport

from .helpers import make_report


class IdentifiersTests(TestCase):
    def test_cle_primaire_uuid(self):
        report = make_report()
        self.assertIsInstance(report.pk, uuid.UUID)
        self.assertIsInstance(report.id, uuid.UUID)

    def test_reference_lisible_et_croissante(self):
        first, second = make_report(), make_report()
        pattern = rf"^ACC-{timezone.localdate().year}-\d{{6}}$"
        self.assertRegex(first.reference, pattern)
        self.assertRegex(second.reference, pattern)
        self.assertNotEqual(first.reference, second.reference)
        self.assertLess(first.reference, second.reference)

    def test_reference_stable_apres_modification(self):
        report = make_report()
        reference = report.reference
        report.description = "Modifié"
        report.save()
        report.refresh_from_db()
        self.assertEqual(report.reference, reference)


class GeometryTests(TestCase):
    def test_latitude_longitude_derivees_de_la_geometrie(self):
        report = make_report(location=Point(1.2314567, 6.1725891, srid=4326))
        report.refresh_from_db()
        self.assertEqual(report.location.srid, 4326)
        self.assertAlmostEqual(report.longitude, 1.231457, places=6)
        self.assertAlmostEqual(report.latitude, 6.172589, places=6)

    def test_resynchronisation_avec_update_fields(self):
        report = make_report()
        report.location = Point(1.30, 6.20, srid=4326)
        report.save(update_fields=["location"])
        report.refresh_from_db()
        self.assertAlmostEqual(report.longitude, 1.30)
        self.assertAlmostEqual(report.latitude, 6.20)

    def test_point_sans_srid_recoit_4326(self):
        report = make_report(location=Point(1.25, 6.15))
        report.refresh_from_db()
        self.assertEqual(report.location.srid, 4326)

    def test_coordonnees_hors_limites_refusees(self):
        report = AccidentReport(
            accident_type=AccidentType.OTHER,
            accident_date=timezone.localdate(),
            accident_time="00:01",
            location=Point(200, 100, srid=4326),
        )
        with self.assertRaises(ValidationError) as ctx:
            report.full_clean()
        self.assertIn("location", ctx.exception.message_dict)

    def test_requete_spatiale_postgis(self):
        inside = make_report(location=Point(1.23, 6.17, srid=4326))
        make_report(location=Point(2.50, 7.50, srid=4326))  # loin de Lomé
        lome_box = Polygon.from_bbox((1.1, 6.0, 1.4, 6.3))
        lome_box.srid = 4326
        found = AccidentReport.objects.filter(location__within=lome_box)
        self.assertEqual(list(found), [inside])


class AnonymityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("90123456")

    def test_signalement_identifie_garde_son_utilisateur(self):
        report = make_report(user=self.user, is_anonymous=False)
        self.assertEqual(report.user, self.user)
        self.assertEqual(list(self.user.reports.all()), [report])

    def test_signalement_anonyme_perd_son_utilisateur(self):
        report = make_report(user=self.user, is_anonymous=True)
        report.refresh_from_db()
        self.assertTrue(report.is_anonymous)
        self.assertIsNone(report.user)

    def test_contrainte_base_de_donnees_anonyme_sans_utilisateur(self):
        report = make_report()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AccidentReport.objects.filter(pk=report.pk).update(is_anonymous=True, user=self.user)

    def test_suppression_utilisateur_conserve_le_signalement(self):
        report = make_report(user=self.user)
        self.user.delete()
        report.refresh_from_db()
        self.assertIsNone(report.user)


class DateValidationTests(TestCase):
    def _report(self, **kw):
        defaults = dict(
            accident_type=AccidentType.OTHER,
            location=Point(1.23, 6.17, srid=4326),
            accident_date=timezone.localdate(),
            accident_time="08:00",
        )
        defaults.update(kw)
        return AccidentReport(**defaults)

    def test_date_future_refusee(self):
        report = self._report(accident_date=timezone.localdate() + timedelta(days=2))
        with self.assertRaises(ValidationError) as ctx:
            report.full_clean()
        self.assertIn("accident_date", ctx.exception.message_dict)

    def test_heure_future_aujourd_hui_refusee(self):
        # Horloge figée à 10:00 : 11:00 est dans le futur, 10:03 reste dans la tolérance de 5 min.
        now = timezone.make_aware(datetime.combine(timezone.localdate(), time(10, 0)))
        with patch("reports.models.timezone.localtime", return_value=now):
            with self.assertRaises(ValidationError) as ctx:
                self._report(accident_time=time(11, 0)).full_clean()
            self.assertIn("accident_date", ctx.exception.message_dict)
            self._report(accident_time=time(10, 3)).full_clean()  # tolérance d'horloge
            self._report(accident_time=time(9, 59)).full_clean()

    def test_hier_accepte(self):
        self._report(accident_date=timezone.localdate() - timedelta(days=1)).full_clean()


class StatusWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("90999999", "motdepasse-solide-42")
        self.report = make_report()

    def test_statut_par_defaut(self):
        self.assertEqual(self.report.status, ReportStatus.PENDING)
        self.assertFalse(self.report.is_demo)

    def test_verification_enregistre_qui_et_quand(self):
        self.report.set_status(ReportStatus.VERIFIED, self.admin, note="Confirmé par la police")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.VERIFIED)
        self.assertEqual(self.report.verified_by, self.admin)
        self.assertIsNotNone(self.report.verified_at)
        self.assertEqual(self.report.admin_note, "Confirmé par la police")

    def test_rejet(self):
        self.report.set_status(ReportStatus.REJECTED, self.admin)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.REJECTED)

    def test_retour_en_attente_efface_le_traitement(self):
        self.report.set_status(ReportStatus.VERIFIED, self.admin)
        self.report.set_status(ReportStatus.PENDING, self.admin)
        self.report.refresh_from_db()
        self.assertIsNone(self.report.verified_by)
        self.assertIsNone(self.report.verified_at)

    def test_statut_invalide(self):
        with self.assertRaises(ValueError):
            self.report.set_status("N_IMPORTE_QUOI", self.admin)


class ChoicesTests(TestCase):
    def test_choix_par_defaut_du_cahier_des_charges(self):
        self.assertEqual(len(AccidentType.values), 8)
        self.assertEqual(
            [label for _, label in Severity.choices], ["Faible", "Moyenne", "Grave", "Très grave"]
        )
        self.assertEqual(
            [label for _, label in ReportStatus.choices], ["En attente", "Vérifié", "Rejeté"]
        )
