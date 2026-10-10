import json
import re
from datetime import timedelta

from django.contrib.gis.geos import Point
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from dashboard.stats import compute_stats
from reports.choices import ACCIDENT_TYPE_COLORS, AccidentType, ReportStatus, Severity, accident_type_color
from reports.models import AccidentReport

from .helpers import PersonasTestCase, make_report

PARIS = Point(2.3522, 48.8566, srid=4326)


def chart_data(response):
    raw = re.search(r'<script id="chart-data" type="application/json">(.*?)</script>', response.content.decode(), re.S).group(1)
    return json.loads(raw)


class ComputeStatsTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.user = User.objects.create_user("90123456")
        d = lambda n: self.today - timedelta(days=n)
        make_report(status="VERIFIED", user=self.user, injured_count=2, death_count=1, accident_type="RUN_OFF_ROAD", severity="SEVERE", accident_date=d(5))
        make_report(status="VERIFIED", is_anonymous=True, injured_count=1, accident_type="ROLLOVER", severity="LOW", accident_date=d(5))
        make_report(status="PENDING", is_anonymous=True, injured_count=3, accident_type="RUN_OFF_ROAD", severity="MEDIUM", accident_date=d(3), location=PARIS)
        make_report(status="REJECTED", user=self.user, injured_count=50, death_count=9, accident_type="OTHER", severity="CRITICAL", accident_date=d(3))

    def stats(self, **kw):
        return compute_stats(AccidentReport.objects.all(), **kw)

    def test_compteurs_de_signalements(self):
        counts = self.stats()["counts"]
        self.assertEqual(counts, {"total": 4, "anonymous": 2, "identified": 2, "pending": 1, "verified": 2, "rejected": 1})

    def test_blesses_et_deces_excluent_les_rejetes(self):
        s = self.stats()
        self.assertEqual((s["accidents"], s["injured"], s["deaths"]), (3, 6, 1))
        self.assertFalse(s["include_rejected"])

    def test_filtre_rejete_les_inclut(self):
        s = compute_stats(AccidentReport.objects.filter(status="REJECTED"), include_rejected=True)
        self.assertEqual((s["accidents"], s["injured"], s["deaths"]), (1, 50, 9))

    def test_hors_zone(self):
        self.assertEqual(self.stats()["outside_zone"], 1)

    def test_series_alignees_sur_les_choix_avec_les_zeros(self):
        types = self.stats()["charts"]["types"]
        self.assertEqual(types["codes"], list(AccidentType.values))
        self.assertEqual(types["colors"], [accident_type_color(code) for code in types["codes"]])
        self.assertEqual(dict(zip(types["codes"], types["values"])),
                         {"COLLISION": 0, "ROLLOVER": 1, "RUN_OFF_ROAD": 2, "LOSS_OF_CONTROL": 0,
                          "INTERSECTION": 0, "PILEUP": 0, "PEDESTRIAN": 0, "OTHER": 0})  # « Autre » (rejeté) exclu
        severities = self.stats()["charts"]["severities"]
        self.assertEqual(dict(zip(severities["codes"], severities["values"])),
                         {"LOW": 1, "MEDIUM": 1, "SEVERE": 1, "CRITICAL": 0})
        self.assertEqual(types["labels"][0], "Collision")

    def test_couleurs_distinctes_et_stables_pour_chaque_type(self):
        colors = [accident_type_color(code) for code in AccidentType.values]
        self.assertEqual(set(ACCIDENT_TYPE_COLORS), set(AccidentType.values))
        self.assertEqual(len(colors), len(set(colors)))
        self.assertEqual(accident_type_color("NEW_ACCIDENT_TYPE"), accident_type_color("NEW_ACCIDENT_TYPE"))
        self.assertTrue(accident_type_color("NEW_ACCIDENT_TYPE").startswith("hsl("))

    def test_coherence_graphiques_et_totaux(self):
        s = self.stats()
        self.assertEqual(sum(s["charts"]["types"]["values"]), s["accidents"])
        self.assertEqual(sum(s["charts"]["severities"]["values"]), s["accidents"])
        self.assertEqual(sum(s["charts"]["period"]["values"]), s["accidents"])
        self.assertEqual(sum(s["charts"]["statuses"]["values"]), s["counts"]["total"])
        self.assertEqual(sum(s["charts"]["modes"]["values"]), s["counts"]["total"])

    def test_statuts_et_modes(self):
        charts = self.stats()["charts"]
        self.assertEqual(charts["statuses"]["codes"], ["PENDING", "VERIFIED", "REJECTED"])
        self.assertEqual(charts["statuses"]["values"], [1, 2, 1])
        self.assertEqual(charts["modes"]["values"], [2, 2])

    def test_periode_courte_par_jour_avec_jours_vides(self):
        period = self.stats()["charts"]["period"]
        self.assertEqual(period["granularity"], "day")
        self.assertEqual(period["values"], [2, 0, 1])  # J-5, J-4 (vide), J-3
        self.assertEqual(period["labels"][0], (self.today - timedelta(days=5)).strftime("%d/%m"))

    def test_periode_longue_par_mois(self):
        make_report(status="VERIFIED", accident_date=self.today - timedelta(days=200))
        period = self.stats()["charts"]["period"]
        self.assertEqual(period["granularity"], "month")
        self.assertEqual(sum(period["values"]), 4)
        self.assertGreaterEqual(len(period["labels"]), 6)
        self.assertRegex(period["labels"][0], r"^\d{2}/\d{4}$")

    def test_base_vide(self):
        AccidentReport.objects.all().delete()
        s = self.stats()
        self.assertEqual(s["counts"]["total"], 0)
        self.assertEqual((s["injured"], s["deaths"], s["accidents"], s["outside_zone"]), (0, 0, 0, 0))
        self.assertEqual(s["charts"]["period"], {"granularity": "day", "labels": [], "values": []})
        self.assertEqual(sum(s["charts"]["types"]["values"]), 0)

    def test_les_chiffres_suivent_les_donnees_reelles(self):
        before = self.stats()["counts"]["verified"]
        make_report(status="VERIFIED", injured_count=7)
        after = self.stats()
        self.assertEqual(after["counts"]["verified"], before + 1)
        self.assertEqual(after["injured"], 13)

    def test_statistiques_sur_un_sous_ensemble(self):
        s = compute_stats(AccidentReport.objects.filter(is_anonymous=True))
        self.assertEqual(s["counts"]["total"], 2)
        self.assertEqual(s["counts"]["identified"], 0)


class StatisticsPagesTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.owner = User.objects.create_user("90123456")
        self.v = make_report(status="VERIFIED", user=self.owner, injured_count=2, accident_type="RUN_OFF_ROAD", accident_date=today - timedelta(days=4))
        self.p = make_report(status="PENDING", is_anonymous=True, injured_count=3, accident_date=today - timedelta(days=2))
        self.p_old = make_report(status="PENDING", injured_count=1, accident_date=today - timedelta(days=9))
        self.r = make_report(status="REJECTED", injured_count=40, death_count=5, accident_date=today - timedelta(days=1), location=PARIS)
        self.login("staff")

    def test_tableau_de_bord_affiche_les_chiffres_reels(self):
        response = self.client.get(reverse("dashboard:index"))
        stats = response.context["stats"]
        self.assertEqual(stats["counts"]["total"], 4)
        for element, value in [("kpi-total", 4), ("kpi-anonymous", 1), ("kpi-identified", 3), ("kpi-pending", 2),
                               ("kpi-verified", 1), ("kpi-rejected", 1), ("kpi-injured", 6), ("kpi-deaths", 0)]:
            self.assertContains(response, f'id="{element}">{value}<')
        data = chart_data(response)
        self.assertEqual(data["statuses"]["values"], [2, 1, 1])
        self.assertEqual(data["types"]["colors"], [accident_type_color(code) for code in AccidentType.values])

    def test_file_de_traitement_les_plus_anciens_d_abord(self):
        pending = list(self.client.get(reverse("dashboard:index")).context["pending_reports"])
        # Même ordre que la vue : date de création, puis pk (deux créations peuvent avoir le même horodatage sous Windows).
        expected = sorted([self.p, self.p_old], key=lambda r: (r.created_at, r.pk))
        self.assertEqual([r.pk for r in pending], [r.pk for r in expected])
        self.assertContains(self.client.get(reverse("dashboard:index")), "Examiner")

    def test_etat_vide(self):
        AccidentReport.objects.all().delete()
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, 'id="kpi-total">0<')
        self.assertContains(response, "Aucun signalement en attente de vérification")

    def test_statistiques_filtrees_par_statut(self):
        stats = self.client.get(reverse("dashboard:statistics"), {"status": "VERIFIED"}).context["stats"]
        self.assertEqual(stats["counts"]["total"], 1)
        self.assertEqual(stats["injured"], 2)

    def test_filtre_rejete_inclut_les_rejetes_dans_les_graphiques(self):
        response = self.client.get(reverse("dashboard:statistics"), {"status": "REJECTED"})
        stats = response.context["stats"]
        self.assertTrue(stats["include_rejected"])
        self.assertEqual((stats["injured"], stats["deaths"]), (40, 5))
        self.assertContains(response, "incluent les signalements rejetés")

    def test_sans_filtre_les_rejetes_sont_exclus_des_graphiques(self):
        response = self.client.get(reverse("dashboard:statistics"))
        self.assertContains(response, "excluent les signalements rejetés")
        self.assertEqual(response.context["stats"]["injured"], 6)

    def test_filtres_mode_zone_type_periode(self):
        url = reverse("dashboard:statistics")
        self.assertEqual(self.client.get(url, {"mode": "anonymous"}).context["stats"]["counts"]["total"], 1)
        self.assertEqual(self.client.get(url, {"zone": "outside"}).context["stats"]["counts"]["total"], 1)
        self.assertEqual(self.client.get(url, {"accident_type": "RUN_OFF_ROAD"}).context["stats"]["counts"]["total"], 1)
        today = timezone.localdate()
        self.assertEqual(self.client.get(url, {"date_from": (today - timedelta(days=3)).isoformat()}).context["stats"]["counts"]["total"], 2)

    def test_filtres_combines(self):
        stats = self.client.get(reverse("dashboard:statistics"), {"status": "PENDING", "mode": "identified"}).context["stats"]
        self.assertEqual(stats["counts"]["total"], 1)

    def test_filtres_invalides_aucun_chiffre_trompeur(self):
        response = self.client.get(reverse("dashboard:statistics"), {"status": "XX"})
        self.assertContains(response, "Filtres invalides")
        self.assertEqual(response.context["stats"]["counts"]["total"], 0)

    def test_graphiques_et_bibliotheque_chargee(self):
        page = self.client.get(reverse("dashboard:statistics"))
        for canvas in ["chart-period", "chart-statuses", "chart-types", "chart-severities", "chart-modes"]:
            self.assertContains(page, f'id="{canvas}"')
        self.assertContains(page, "vendor/chartjs/chart.umd.js")
        self.assertContains(page, "js/dashboard_charts.js")
        self.assertEqual(set(chart_data(page)), {"period", "types", "severities", "statuses", "modes"})
        self.assertEqual(chart_data(page)["types"]["colors"], [accident_type_color(code) for code in AccidentType.values])

    def test_zone_mentionnee_comme_indicative(self):
        self.assertContains(self.client.get(reverse("dashboard:statistics")), "non officielle")
