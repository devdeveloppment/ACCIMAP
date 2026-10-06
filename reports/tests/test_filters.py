from datetime import timedelta

from django.contrib.gis.geos import Point
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from reports.choices import ReportStatus
from reports.filters import PublicReportFilterForm, ReportFilterForm
from reports.models import AccidentReport

from .helpers import make_report

PARIS = Point(2.3522, 48.8566, srid=4326)


def apply(form_class, **data):
    form = form_class(data)
    assert form.is_valid(), form.errors
    return form.apply(AccidentReport.objects.all())


class AdminFilterTests(TestCase):
    def setUp(self):
        self.citizen = User.objects.create_user("90123456")
        self.pending = make_report(status=ReportStatus.PENDING, user=self.citizen)
        self.verified = make_report(status=ReportStatus.VERIFIED, is_anonymous=True)
        self.rejected = make_report(status=ReportStatus.REJECTED, location=PARIS)

    def test_sans_filtre_tout_est_retourne(self):
        self.assertEqual(apply(ReportFilterForm).count(), 3)

    def test_statut(self):
        for status, expected in [("PENDING", self.pending), ("VERIFIED", self.verified), ("REJECTED", self.rejected)]:
            self.assertEqual(list(apply(ReportFilterForm, status=status)), [expected])

    def test_mode_identifie_anonyme(self):
        self.assertEqual(list(apply(ReportFilterForm, mode="anonymous")), [self.verified])
        self.assertCountEqual(apply(ReportFilterForm, mode="identified"), [self.pending, self.rejected])

    def test_zone_dynamique_dans_et_hors_zone(self):
        self.assertEqual(list(apply(ReportFilterForm, zone="outside")), [self.rejected])
        self.assertCountEqual(apply(ReportFilterForm, zone="inside"), [self.pending, self.verified])

    def test_combinaison_statut_et_mode(self):
        self.assertEqual(list(apply(ReportFilterForm, status="VERIFIED", mode="anonymous")), [self.verified])
        self.assertEqual(list(apply(ReportFilterForm, status="PENDING", mode="anonymous")), [])

    def test_periode_type_gravite(self):
        old = make_report(accident_date=timezone.localdate() - timedelta(days=100),
                          accident_type="ROLLOVER", severity="CRITICAL")
        recent = timezone.localdate() - timedelta(days=50)
        self.assertEqual(list(apply(ReportFilterForm, date_to=recent.isoformat())), [old])
        self.assertEqual(list(apply(ReportFilterForm, accident_type="ROLLOVER")), [old])
        self.assertEqual(list(apply(ReportFilterForm, severity="CRITICAL")), [old])

    def test_periode_inversee_invalide(self):
        form = ReportFilterForm({"date_from": "2026-05-10", "date_to": "2026-05-01"})
        self.assertFalse(form.is_valid())
        self.assertIn("date_to", form.errors)

    def test_valeurs_invalides(self):
        for data in [{"status": "X"}, {"mode": "X"}, {"zone": "X"}, {"severity": "X"}, {"accident_type": "X"}]:
            self.assertFalse(ReportFilterForm(data).is_valid(), data)


class PublicFilterFormTests(TestCase):
    def test_champs_administrateur_supprimes(self):
        form = PublicReportFilterForm()
        self.assertEqual(set(form.fields), {"date_from", "date_to", "accident_type", "severity"})

    def test_champs_administrateur_ignores_s_ils_sont_envoyes(self):
        make_report(status=ReportStatus.REJECTED)
        form = PublicReportFilterForm({"status": "REJECTED", "mode": "anonymous", "zone": "outside"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.apply(AccidentReport.objects.all()).count(), 1)  # aucun effet
