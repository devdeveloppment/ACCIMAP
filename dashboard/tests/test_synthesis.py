"""
Tests de SYNTHÈSE (phase 8) : les modules enchaînés par leurs vraies vues HTTP, sans raccourci de connexion.

    citoyen -> OTP (accounts) -> signalement (reports) -> connexion privée par mot de passe (dashboard)
    -> validation -> carte publique (maps) -> statistiques (dashboard) -> exports CSV / Excel / PDF (exports)

À chaque étape : le résultat attendu, les permissions et l'absence de fuite de données personnelles.
"""
import io
import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook
from pypdf import PdfReader

from accounts.models import User
from accounts.roles import ROLE_STAFF, ROLE_SUPERUSER, set_role
from reports.choices import ReportStatus
from reports.models import AccidentReport
from reports.tests.helpers import valid_report_data

CITIZEN_PHONE = "90112233"
CITIZEN_FULL = "+22890112233"
ADMIN_PHONE = "90445566"
ADMIN_PASSWORD = "Synthese-Accimap-2026!"
PRIVATE_TEXT = "Appeler Kossi au 90112233, plaque TG-4321-AB"
NOTE = "NOTE-INTERNE-SYNTHESE"


def make_admin(phone, role=ROLE_SUPERUSER, password=ADMIN_PASSWORD, **options):
    user = User.objects.create_user(phone)
    set_role(user, role, **options)
    user = User.objects.get(pk=user.pk)
    user.set_password(password)
    user.save(update_fields=["password"])
    return user


def otp_login(client, phone, next_url="/report/"):
    """Connexion citoyenne réelle : numéro -> code (affiché en mode dev) -> vérification."""
    client.get(next_url)
    client.post("/login/", {"phone_number": phone, "next": next_url})
    code = client.session["otp_dev_code"]
    return client.post("/verify-otp/", {"code": code})


def admin_login(client, phone=ADMIN_PHONE, password=ADMIN_PASSWORD):
    return client.post(reverse("dashboard:login"), {"username": phone, "password": password})


def public_features(client=None):
    response = (client or Client()).get(reverse("maps:public_data"))
    assert response.status_code == 200, response.status_code
    return response, response.json()


@override_settings(OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=0, REVERSE_GEOCODING_ENABLED=False)
class FullJourneyTests(TestCase):
    """Le parcours complet, du signalement public jusqu'aux exports."""

    def setUp(self):
        cache.clear()   # compteurs anti-force-brute de la connexion privée
        self.admin = make_admin(ADMIN_PHONE)
        self.citizen_client = Client()
        self.admin_client = Client()

    def _submit_public_report(self):
        response = self.citizen_client.post(
            reverse("reports:create"), valid_report_data(description=PRIVATE_TEXT)
        )
        self.assertEqual(response.status_code, 302)
        self.success_url = response["Location"]   # confirmation protégée par un jeton signé
        return AccidentReport.objects.get()

    def test_parcours_complet(self):
        # 1. Visiteur non connecté : signalement public enregistré anonymement
        report = self._submit_public_report()
        self.assertTrue(report.is_anonymous)
        self.assertIsNone(report.user)
        self.assertFalse(User.objects.filter(phone_number=CITIZEN_FULL).exists())
        self.assertEqual(report.status, ReportStatus.PENDING)
        self.assertEqual((round(report.location.y, 6), round(report.location.x, 6)), (6.1319, 1.2228))
        self.assertEqual(report.location.srid, 4326)
        success = self.citizen_client.get(self.success_url)
        self.assertContains(success, report.reference)
        self.assertNotContains(success, CITIZEN_FULL)
        # Sans jeton valide, la page de confirmation ne révèle rien
        self.assertRedirects(self.citizen_client.get(reverse("reports:success")), "/",
                             fetch_redirect_response=False)

        # 2. Avant validation : absent de la carte publique
        _, data = public_features()
        self.assertEqual(data["features"], [])

        # 3. Un visiteur ne peut pas accéder à l'espace privé
        self.assertEqual(self.citizen_client.get(reverse("dashboard:index")).status_code, 302)
        self.assertEqual(self.citizen_client.get(reverse("exports:xlsx")).status_code, 302)
        self.assertEqual(self.citizen_client.get(reverse("dashboard:map_data")).status_code, 401)
        self.assertEqual(self.citizen_client.post(reverse("dashboard:report_review", args=[report.pk]),
                                                  {"status": ReportStatus.VERIFIED}).status_code, 302)

        # 4. Connexion privée par identifiant + mot de passe
        self.assertRedirects(admin_login(self.admin_client), reverse("dashboard:index"),
                             fetch_redirect_response=False)
        index = self.admin_client.get(reverse("dashboard:index"))
        self.assertEqual(index.status_code, 200)
        self.assertEqual(index.context["stats"]["counts"]["pending"], 1)
        self.assertIn(report, list(index.context["pending_reports"]))

        stats_before = self.admin_client.get(reverse("dashboard:statistics")).context["stats"]
        self.assertEqual(stats_before["counts"]["verified"], 0)

        # 5. Fiche et validation
        detail = self.admin_client.get(reverse("dashboard:report_detail", args=[report.pk]))
        self.assertContains(detail, report.reference)
        response = self.admin_client.post(
            reverse("dashboard:report_review", args=[report.pk]),
            {"status": ReportStatus.VERIFIED, "admin_note": NOTE},
        )
        self.assertRedirects(response, reverse("dashboard:report_detail", args=[report.pk]),
                             fetch_redirect_response=False)
        report.refresh_from_db()
        self.assertEqual(report.status, ReportStatus.VERIFIED)
        self.assertEqual(report.verified_by, self.admin)
        self.assertIsNotNone(report.verified_at)
        self.assertEqual(report.admin_note, NOTE)

        # 6. Apparition sur la carte publique, coordonnées identiques au PointField, sans donnée personnelle
        response, data = public_features()
        self.assertEqual(len(data["features"]), 1)
        feature = data["features"][0]
        self.assertEqual(feature["geometry"], {"type": "Point", "coordinates": [1.2228, 6.1319]})
        self.assertEqual(feature["properties"]["type"], "RUN_OFF_ROAD")
        body = response.content.decode()
        for forbidden in [CITIZEN_FULL, CITIZEN_PHONE, "Kossi", "TG-4321-AB", NOTE, report.reference,
                          "photo", "admin_note", str(report.pk)]:
            self.assertNotIn(forbidden, body)

        # La carte administrative voit le même point
        admin_data = self.admin_client.get(reverse("dashboard:map_data")).json()
        self.assertEqual(len(admin_data["features"]), 1)

        # 7. Statistiques à jour
        stats = self.admin_client.get(reverse("dashboard:statistics")).context["stats"]
        self.assertEqual(stats["counts"]["verified"], 1)
        self.assertEqual(stats["counts"]["pending"], 0)
        self.assertEqual(stats["counts"]["anonymous"], 1)
        self.assertEqual(stats["counts"]["identified"], 0)
        self.assertEqual(stats["injured"], 2)
        self.assertEqual(stats["outside_zone"], 0)
        verified_only = self.admin_client.get(
            reverse("dashboard:statistics"), {"status": ReportStatus.VERIFIED}
        ).context["stats"]
        self.assertEqual(verified_only["counts"]["total"], 1)

        # 8. Exports Excel, PDF et CSV du signalement validé
        xlsx = self.admin_client.get(reverse("exports:xlsx"), {"status": ReportStatus.VERIFIED})
        self.assertEqual(xlsx.status_code, 200)
        self.assertIn("attachment", xlsx["Content-Disposition"])
        sheet = load_workbook(io.BytesIO(xlsx.content)).active
        rows = list(sheet.iter_rows(values_only=True))
        headers = list(rows[0])
        record = dict(zip(headers, next(r for r in rows[1:] if r[0] == report.reference)))
        self.assertEqual(record["Statut"], "Vérifié")
        self.assertEqual(record["Mode"], "Anonyme")
        self.assertAlmostEqual(record["Latitude"], 6.1319)
        self.assertAlmostEqual(record["Longitude"], 1.2228)
        self.assertEqual(record["Zone de couverture"], "Dans la zone")
        self.assertEqual(record["Blessés"], 2)
        workbook_text = json.dumps([list(map(str, r)) for r in rows])
        self.assertNotIn(CITIZEN_FULL, workbook_text)   # le numéro du citoyen n'est jamais exporté
        self.assertNotIn(NOTE, workbook_text)           # ni la note interne

        pdf = self.admin_client.get(reverse("exports:pdf"), {"status": ReportStatus.VERIFIED})
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
        self.assertIn(report.reference, "".join(text.split()))
        self.assertNotIn(CITIZEN_FULL, text)

        csv_response = self.admin_client.get(reverse("exports:csv"))
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn(report.reference, csv_response.content.decode("utf-8-sig"))

        # 9. Rejet ultérieur : le point disparaît de la carte publique, les statistiques suivent
        self.admin_client.post(reverse("dashboard:report_review", args=[report.pk]),
                               {"status": ReportStatus.REJECTED, "admin_note": ""})
        _, data = public_features()
        self.assertEqual(data["features"], [])
        stats = self.admin_client.get(reverse("dashboard:statistics")).context["stats"]
        self.assertEqual((stats["counts"]["rejected"], stats["accidents"]), (1, 0))

    def test_signalement_anonyme_valide_puis_publie(self):
        anonymous = Client()
        response = anonymous.post(reverse("reports:anonymous_create"),
                                  valid_report_data(latitude="6.150000", longitude="1.250000"))
        self.assertEqual(response.status_code, 302)
        report = AccidentReport.objects.get()
        self.assertTrue(report.is_anonymous)
        self.assertIsNone(report.user)

        admin_login(self.admin_client)
        self.admin_client.post(reverse("dashboard:report_review", args=[report.pk]),
                               {"status": ReportStatus.VERIFIED, "admin_note": ""})
        _, data = public_features()
        self.assertEqual(data["features"][0]["geometry"]["coordinates"], [1.25, 6.15])
        stats = self.admin_client.get(reverse("dashboard:statistics")).context["stats"]
        self.assertEqual((stats["counts"]["anonymous"], stats["counts"]["identified"]), (1, 0))

    def test_position_hors_zone_conservee_et_signalee(self):
        """Hors zone indicative : avertissement, jamais de refus ni de correction des coordonnées."""
        anonymous = Client()
        check = anonymous.get(reverse("reports:check_position"), {"latitude": "6.5", "longitude": "1.6"})
        self.assertEqual(check.status_code, 200)
        self.assertFalse(check.json()["inside_zone"])
        anonymous.post(reverse("reports:anonymous_create"),
                       valid_report_data(latitude="6.500000", longitude="1.600000",
                                         confirm_outside_zone="on"))
        report = AccidentReport.objects.get()
        self.assertEqual((report.location.y, report.location.x), (6.5, 1.6))
        admin_login(self.admin_client)
        self.assertEqual(self.admin_client.get(reverse("dashboard:statistics")).context["stats"]["outside_zone"], 1)

    def test_position_invalide_refusee(self):
        anonymous = Client()
        for lat, lon in [("95", "1.2"), ("6.1", "200"), ("abc", "1.2"), ("", "")]:
            response = anonymous.post(reverse("reports:anonymous_create"),
                                      valid_report_data(latitude=lat, longitude=lon))
            self.assertEqual(response.status_code, 200, (lat, lon))
        self.assertEqual(AccidentReport.objects.count(), 0)


@override_settings(OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=0, REVERSE_GEOCODING_ENABLED=False)
class CrossModulePermissionTests(TestCase):
    """Permissions vérifiées à la jonction des modules, avec de vraies connexions."""

    def setUp(self):
        cache.clear()
        self.report = AccidentReport.objects.create(
            accident_type="COLLISION", accident_date="2026-01-10", accident_time="10:00",
            severity="MEDIUM", location="SRID=4326;POINT(1.2228 6.1319)",
        )

    def test_staff_lecture_seule_ne_peut_ni_valider_ni_rien_changer(self):
        make_admin("90770001", ROLE_STAFF, can_change=False, can_delete=False)
        client = Client()
        admin_login(client, "90770001")
        self.assertEqual(client.get(reverse("dashboard:report_detail", args=[self.report.pk])).status_code, 200)
        response = client.post(reverse("dashboard:report_review", args=[self.report.pk]),
                               {"status": ReportStatus.VERIFIED, "admin_note": ""})
        self.assertEqual(response.status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.PENDING)
        self.assertEqual(public_features()[1]["features"], [])
        # Export autorisé avec la seule permission « voir » (README, tableau des niveaux d'accès),
        # toujours sans numéro de téléphone
        export = client.get(reverse("exports:csv"))
        self.assertEqual(export.status_code, 200)
        self.assertNotIn("+228", export.content.decode("utf-8-sig"))

    def test_staff_connecte_par_otp_n_a_pas_acces_a_l_espace_prive(self):
        """Un compte staff qui passe par l'OTP citoyen reste un simple citoyen."""
        make_admin("90770002", ROLE_SUPERUSER)
        client = Client()
        otp_login(client, "90770002", next_url="/")
        self.assertTrue(client.session.get("_auth_user_id"))
        self.assertEqual(client.get(reverse("dashboard:index")).status_code, 302)
        self.assertEqual(client.get(reverse("exports:pdf")).status_code, 302)
        self.assertEqual(client.get(reverse("dashboard:map_data")).status_code, 401)
        self.assertEqual(client.post(reverse("dashboard:report_review", args=[self.report.pk]),
                                     {"status": ReportStatus.VERIFIED}).status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.PENDING)

    def test_citoyen_sans_mot_de_passe_ne_peut_pas_ouvrir_l_espace_prive(self):
        otp_login(Client(), CITIZEN_PHONE, next_url="/")
        client = Client()
        response = admin_login(client, CITIZEN_PHONE, "nimporte-quoi")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get(reverse("dashboard:index")).status_code, 302)

    def test_visiteur_anonyme(self):
        client = Client()
        self.assertEqual(client.get(reverse("maps:public_map")).status_code, 200)
        self.assertEqual(client.get(reverse("dashboard:statistics")).status_code, 302)
        self.assertEqual(client.get(reverse("exports:csv")).status_code, 302)
        self.assertEqual(client.get(reverse("dashboard:report_photo", args=[self.report.pk])).status_code, 302)
        self.assertEqual(client.get("/admin/").status_code, 302)
        self.assertEqual(client.get("/media/anything.jpg").status_code, 404)
