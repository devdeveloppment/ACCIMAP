import json
from datetime import date, timedelta

from django.contrib.gis.geos import Point
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from reports.choices import ReportStatus
from reports.models import AccidentReport
from reports.tests.helpers import make_report

URL = reverse("maps:public_data")
FEATURE_KEYS = {"type", "geometry", "properties"}
PROPERTY_KEYS = {"type", "type_label", "severity", "severity_label", "date", "time", "injured", "deaths"}


def fetch(client, **params):
    response = client.get(URL, params)
    return response, json.loads(response.content)


class StatusVisibilityTests(TestCase):
    """Vérifié -> affiché ; En attente et Rejeté -> JAMAIS affichés."""

    def setUp(self):
        self.admin = User.objects.create_superuser("90999999", "motdepasse-solide-42")
        self.verified = make_report(status=ReportStatus.VERIFIED, injured_count=1)
        self.pending = make_report(status=ReportStatus.PENDING, injured_count=2)
        self.rejected = make_report(status=ReportStatus.REJECTED, injured_count=3)

    def injured(self, data):
        return sorted(f["properties"]["injured"] for f in data["features"])

    def test_seuls_les_verifies_sont_publics(self):
        response, data = fetch(self.client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.injured(data), [1])
        self.assertEqual(data["meta"]["count"], 1)

    def test_queryset_public(self):
        self.assertEqual(list(AccidentReport.objects.public()), [self.verified])

    def test_aucun_filtre_ne_revele_attente_ou_rejete(self):
        for params in [{}, {"severity": "MEDIUM"}, {"accident_type": "COLLISION"},
                       {"date_from": "2000-01-01"}, {"status": "PENDING"}, {"status": "REJECTED"}]:
            with self.subTest(params=params):
                _, data = fetch(self.client, **params)
                self.assertEqual(self.injured(data), [1])

    def test_meme_connecte_en_administrateur_la_carte_publique_reste_publique(self):
        self.client.force_login(self.admin)
        _, data = fetch(self.client)
        self.assertEqual(self.injured(data), [1])

    def test_proprietaire_ne_voit_pas_son_signalement_en_attente(self):
        owner = User.objects.create_user("90123456")
        make_report(user=owner, status=ReportStatus.PENDING, injured_count=9)
        self.client.force_login(owner)
        _, data = fetch(self.client)
        self.assertNotIn(9, self.injured(data))

    def test_la_verification_fait_apparaitre_le_signalement(self):
        self.pending.set_status(ReportStatus.VERIFIED, self.admin)
        _, data = fetch(self.client)
        self.assertEqual(self.injured(data), [1, 2])

    def test_type_autre_expose_sa_precision_dans_le_libelle(self):
        report = make_report(
            status=ReportStatus.VERIFIED,
            accident_type="OTHER",
            accident_cause="EXCESSIVE_SPEED",
        )
        _, data = fetch(self.client)
        feature = next(f for f in data["features"] if f["properties"]["type"] == "OTHER")
        self.assertEqual(feature["properties"]["type_label"], "Vitesse excessive")
        self.assertEqual(report.accident_type, "OTHER")

    def test_le_rejet_ou_la_remise_en_attente_le_retire(self):
        self.verified.set_status(ReportStatus.REJECTED, self.admin)
        self.assertEqual(self.injured(fetch(self.client)[1]), [])
        self.verified.set_status(ReportStatus.VERIFIED, self.admin)
        self.assertEqual(self.injured(fetch(self.client)[1]), [1])
        self.verified.set_status(ReportStatus.PENDING, self.admin)
        self.assertEqual(self.injured(fetch(self.client)[1]), [])

    def test_aucun_signalement(self):
        AccidentReport.objects.all().delete()
        response, data = fetch(self.client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["features"], [])
        self.assertEqual(data["meta"], {"count": 0, "truncated": False})


class PrivacyTests(TestCase):
    """Rien d'identifiant ne doit sortir, et anonyme/identifié sont indiscernables."""

    SECRET_NOTE = "NOTE-CONFIDENTIELLE-ADMIN"
    DESCRIPTION = "Appeler Jean Dupont au 90123456, plaque TG-1234-AB"

    def setUp(self):
        self.admin = User.objects.create_superuser("90999999", "motdepasse-solide-42")
        self.citizen = User.objects.create_user("90123456")
        common = dict(status=ReportStatus.VERIFIED, description=self.DESCRIPTION,
                      admin_note=self.SECRET_NOTE, verified_by=self.admin, injured_count=2)
        self.identified = make_report(user=self.citizen, is_anonymous=False, **common)
        self.anonymous = make_report(is_anonymous=True, location=Point(1.25, 6.15, srid=4326), **common)
        self.response, self.data = fetch(self.client)
        self.body = self.response.content.decode()

    def test_deux_signalements_publics(self):
        self.assertEqual(self.data["meta"]["count"], 2)

    def test_aucun_numero_ni_identite(self):
        for forbidden in ["90123456", "90999999", "+228", "Dupont"]:
            self.assertNotIn(forbidden, self.body, forbidden)

    def test_ni_identifiant_ni_reference_ni_note_ni_photo(self):
        for report in (self.identified, self.anonymous):
            self.assertNotIn(str(report.id), self.body)
            self.assertNotIn(report.reference, self.body)
        self.assertNotIn("ACC-", self.body)
        self.assertNotIn(self.SECRET_NOTE, self.body)
        for key in ["user", "phone", "anonymous", "photo", "created_at", "updated_at",
                    "verified", "admin_note", "reference", "status", "is_demo", "latitude", "longitude"]:
            self.assertNotIn(f'"{key}"', self.body, key)

    def test_description_non_publiee_par_defaut(self):
        self.assertNotIn("TG-1234-AB", self.body)
        self.assertNotIn("description", self.body)

    @override_settings(PUBLIC_MAP_SHOW_DESCRIPTION=True)
    def test_description_publiee_seulement_si_autorisee(self):
        _, data = fetch(self.client)
        self.assertTrue(all("description" in f["properties"] for f in data["features"]))

    def test_structure_exacte_liste_blanche(self):
        self.assertEqual(set(self.data), {"type", "features", "meta"})
        for feature in self.data["features"]:
            self.assertEqual(set(feature), FEATURE_KEYS)
            self.assertEqual(set(feature["properties"]), PROPERTY_KEYS)

    def test_anonyme_et_identifie_ont_exactement_la_meme_forme(self):
        shapes = {
            (tuple(sorted(f["properties"])), tuple(sorted(f)), type(f["properties"]["injured"]).__name__)
            for f in self.data["features"]
        }
        self.assertEqual(len(shapes), 1)

    def test_filtres_reserves_a_l_administrateur_sans_effet_ni_fuite(self):
        _, baseline = fetch(self.client)
        for params in [{"mode": "anonymous"}, {"mode": "identified"}, {"zone": "outside"},
                       {"zone": "inside"}, {"status": "PENDING"}, {"is_anonymous": "true"}, {"user": "1"}]:
            with self.subTest(params=params):
                _, data = fetch(self.client, **params)
                self.assertEqual(data, baseline)  # paramètre ignoré : impossible d'isoler les anonymes


class GeometryTests(TestCase):
    def test_coordonnees_geojson_lon_lat_depuis_le_pointfield(self):
        make_report(status=ReportStatus.VERIFIED, location=Point(1.2231, 6.1319, srid=4326))
        _, data = fetch(self.client)
        geometry = data["features"][0]["geometry"]
        self.assertEqual(geometry["type"], "Point")
        self.assertEqual(geometry["coordinates"], [1.2231, 6.1319])  # [longitude, latitude]

    def test_precision_six_decimales(self):
        make_report(status=ReportStatus.VERIFIED, location=Point(1.123456789, 6.987654321, srid=4326))
        _, data = fetch(self.client)
        self.assertEqual(data["features"][0]["geometry"]["coordinates"], [1.123457, 6.987654])

    def test_la_source_est_le_pointfield_pas_les_colonnes_derivees(self):
        report = make_report(status=ReportStatus.VERIFIED, location=Point(1.2231, 6.1319, srid=4326))
        AccidentReport.objects.filter(pk=report.pk).update(latitude=0.0, longitude=0.0)
        _, data = fetch(self.client)
        self.assertEqual(data["features"][0]["geometry"]["coordinates"], [1.2231, 6.1319])

    def test_contenu_des_proprietes(self):
        make_report(status=ReportStatus.VERIFIED, accident_type="RUN_OFF_ROAD", severity="CRITICAL",
                    accident_date=date(2026, 9, 20), accident_time="18:45", injured_count=3, death_count=1)
        props = fetch(self.client)[1]["features"][0]["properties"]
        self.assertEqual(props, {"type": "RUN_OFF_ROAD", "type_label": "Sortie de voie",
                                 "severity": "CRITICAL", "severity_label": "Très grave",
                                 "date": "2026-09-20", "time": "18:45", "injured": 3, "deaths": 1})

    def test_api_transmet_la_gravite_reelle_de_chaque_signalement(self):
        severities = ("CRITICAL", "SEVERE", "MEDIUM", "LOW")
        for index, severity in enumerate(severities, start=1):
            make_report(status=ReportStatus.VERIFIED, severity=severity, injured_count=index)
        _, data = fetch(self.client)
        by_report = {feature["properties"]["injured"]: feature["properties"]["severity"] for feature in data["features"]}
        self.assertEqual(by_report, {index: severity for index, severity in enumerate(severities, start=1)})
        for severity in severities:
            with self.subTest(severity=severity):
                filtered = fetch(self.client, severity=severity)[1]["features"]
                self.assertTrue(filtered)
                self.assertTrue(all(feature["properties"]["severity"] == severity for feature in filtered))


class FilterTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.d = lambda days: today - timedelta(days=days)
        self.a = make_report(status=ReportStatus.VERIFIED, accident_date=self.d(40), accident_type="RUN_OFF_ROAD", severity="LOW", injured_count=1)
        self.b = make_report(status=ReportStatus.VERIFIED, accident_date=self.d(20), accident_type="RUN_OFF_ROAD", severity="SEVERE", injured_count=2)
        self.c = make_report(status=ReportStatus.VERIFIED, accident_date=self.d(5), accident_type="ROLLOVER", severity="SEVERE", injured_count=3)

    def ids(self, **params):
        response, data = fetch(self.client, **params)
        self.assertEqual(response.status_code, 200, data)
        return sorted(f["properties"]["injured"] for f in data["features"])

    def test_periode_bornes_incluses(self):
        self.assertEqual(self.ids(date_from=self.d(20).isoformat()), [2, 3])
        self.assertEqual(self.ids(date_to=self.d(20).isoformat()), [1, 2])
        self.assertEqual(self.ids(date_from=self.d(20).isoformat(), date_to=self.d(20).isoformat()), [2])
        self.assertEqual(self.ids(date_from=self.d(30).isoformat(), date_to=self.d(10).isoformat()), [2])

    def test_type(self):
        self.assertEqual(self.ids(accident_type="RUN_OFF_ROAD"), [1, 2])
        self.assertEqual(self.ids(accident_type="ROLLOVER"), [3])
        self.assertEqual(self.ids(accident_type="OTHER"), [])

    def test_gravite(self):
        self.assertEqual(self.ids(severity="SEVERE"), [2, 3])
        self.assertEqual(self.ids(severity="LOW"), [1])
        for severity, expected in (("LOW", [1]), ("SEVERE", [2, 3])):
            with self.subTest(severity=severity):
                _, data = fetch(self.client, severity=severity)
                self.assertTrue(all(feature["properties"]["severity"] == severity for feature in data["features"]))
                self.assertEqual(sorted(feature["properties"]["injured"] for feature in data["features"]), expected)

    def test_combinaison(self):
        self.assertEqual(self.ids(accident_type="RUN_OFF_ROAD", severity="SEVERE"), [2])
        self.assertEqual(self.ids(accident_type="RUN_OFF_ROAD", severity="SEVERE", date_from=self.d(5).isoformat()), [])

    def test_valeurs_vides_ignorees(self):
        self.assertEqual(self.ids(accident_type="", severity="", date_from="", date_to=""), [1, 2, 3])

    def test_filtres_invalides_refuses(self):
        for params in [{"severity": "TERRIBLE"}, {"accident_type": "INCONNU"}, {"date_from": "hier"},
                       {"date_to": "2026-13-45"}]:
            with self.subTest(params=params):
                response, data = fetch(self.client, **params)
                self.assertEqual(response.status_code, 400)
                self.assertIn("fields", data)

    def test_periode_inversee_refusee_avec_message(self):
        response, data = fetch(self.client, date_from=self.d(5).isoformat(), date_to=self.d(20).isoformat())
        self.assertEqual(response.status_code, 400)
        self.assertIn("date_to", data["fields"])


class ApiBehaviourTests(TestCase):
    def test_get_uniquement(self):
        self.assertEqual(self.client.post(URL).status_code, 405)

    def test_type_de_contenu_et_cache(self):
        response = self.client.get(URL)
        self.assertEqual(response["Content-Type"], "application/geo+json")
        self.assertIn("no-cache", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_accessible_sans_connexion(self):
        self.assertEqual(self.client.get(URL).status_code, 200)

    def test_tri_du_plus_recent_au_plus_ancien(self):
        today = timezone.localdate()
        for days, injured in [(30, 1), (2, 3), (10, 2)]:
            make_report(status=ReportStatus.VERIFIED, accident_date=today - timedelta(days=days), injured_count=injured)
        order = [f["properties"]["injured"] for f in fetch(self.client)[1]["features"]]
        self.assertEqual(order, [3, 2, 1])

    @override_settings(PUBLIC_MAP_MAX_FEATURES=2)
    def test_limite_du_nombre_de_points(self):
        for _ in range(3):
            make_report(status=ReportStatus.VERIFIED)
        _, data = fetch(self.client)
        self.assertEqual(data["meta"], {"count": 2, "truncated": True})
        self.assertEqual(len(data["features"]), 2)

    def test_une_seule_requete_sql_quel_que_soit_le_nombre_de_points(self):
        for _ in range(8):
            make_report(status=ReportStatus.VERIFIED)
        with self.assertNumQueries(1):
            self.client.get(URL)
