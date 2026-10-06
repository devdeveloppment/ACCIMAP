import io
import json
import re
import tempfile
from unittest.mock import patch

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import User
from reports.choices import ReportStatus
from reports.models import AccidentReport

from .helpers import make_jpeg_with_exif, valid_report_data
from .test_zone import SQUARE, write_geojson

PHONE = "+22890123456"


def follow_success(response):
    """Suit la redirection vers la page de confirmation."""
    return response.client.get(response["Location"])


class IdentifiedReportJourneyTests(TestCase):
    """Connexion -> /report/ -> formulaire -> enregistrement -> confirmation."""

    def setUp(self):
        self.user = User.objects.create_user("90123456")
        self.url = reverse("reports:create")

    def test_visiteur_non_connecte_est_envoye_vers_la_connexion(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, "/login/?next=/report/")
        self.assertEqual(self.client.post(self.url, valid_report_data()).status_code, 302)
        self.assertEqual(AccidentReport.objects.count(), 0)

    def test_formulaire_affiche_pour_un_utilisateur_connecte(self):
        self.client.force_login(self.user)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        for expected in ["Utiliser ma position actuelle", 'id="location-map"', 'id="id_latitude"',
                         'id="id_longitude"', "Latitude", "Longitude", "Collision",
                         "Très grave", "Photographie (facultative)", "+228****56"]:
            self.assertContains(page, expected)
        self.assertNotContains(page, PHONE)  # numéro jamais affiché en entier

    def test_enregistrement_identifie(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, valid_report_data())
        self.assertEqual(response.status_code, 302)

        report = AccidentReport.objects.get()
        self.assertEqual(report.user, self.user)
        self.assertFalse(report.is_anonymous)
        self.assertEqual(report.status, ReportStatus.PENDING)
        self.assertFalse(report.is_demo)
        self.assertEqual(report.accident_type, "RUN_OFF_ROAD")
        self.assertEqual(report.severity, "SEVERE")
        self.assertEqual(report.injured_count, 2)
        self.assertAlmostEqual(report.location.y, 6.1319)
        self.assertAlmostEqual(report.location.x, 1.2228)
        self.assertRegex(report.reference, r"^ACC-\d{4}-\d{6}$")

    def test_confirmation(self):
        self.client.force_login(self.user)
        page = follow_success(self.client.post(self.url, valid_report_data()))
        self.assertContains(page, "Votre signalement a été enregistré.")
        self.assertContains(page, "Votre signalement est en attente de vérification.")
        self.assertNotContains(page, "enregistré anonymement")
        self.assertContains(page, AccidentReport.objects.get().reference)
        self.assertNotContains(page, PHONE)

    def test_connexion_otp_puis_retour_au_formulaire(self):
        """Parcours réel : /report/ -> connexion par OTP -> retour sur /report/."""
        with override_settings(OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=0):
            self.client.get(self.url)
            self.client.post("/login/", {"phone_number": "90123456", "next": "/report/"})
            code = self.client.session["otp_dev_code"]
            response = self.client.post("/verify-otp/", {"code": code})
        self.assertRedirects(response, "/report/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/report/").status_code, 200)

    def test_formulaire_invalide_ne_cree_rien_et_conserve_la_saisie(self):
        self.client.force_login(self.user)
        data = valid_report_data(accident_type="", description="Texte à conserver")
        page = self.client.post(self.url, data)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(AccidentReport.objects.count(), 0)
        self.assertContains(page, "Texte à conserver")
        self.assertContains(page, 'value="6.131900"')   # la position choisie n'est pas perdue
        self.assertContains(page, 'value="1.222800"')

    def test_csrf_obligatoire(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(self.url, valid_report_data()).status_code, 403)
        self.assertEqual(AccidentReport.objects.count(), 0)


class FormPrefillTests(TestCase):
    """Les champs date/heure doivent être préremplis dans un format que le navigateur accepte."""

    def test_date_et_heure_preremplies_au_format_html5(self):
        from django.utils import timezone

        page = self.client.get(reverse("reports:anonymous_create")).content.decode()
        today = timezone.localdate().isoformat()  # AAAA-MM-JJ, jamais JJ/MM/AAAA
        self.assertRegex(page, rf'<input[^>]*name="accident_date"[^>]*value="{today}"')
        self.assertRegex(page, r'<input[^>]*name="accident_time"[^>]*value="\d{2}:\d{2}"')
        self.assertNotRegex(page, r'name="accident_date"[^>]*value="\d{2}/\d{2}/\d{4}"')

    def test_valeurs_conservees_apres_une_erreur(self):
        data = valid_report_data(accident_type="", accident_date="2026-09-15", accident_time="07:30")
        page = self.client.post(reverse("reports:anonymous_create"), data).content.decode()
        self.assertRegex(page, r'<input[^>]*name="accident_date"[^>]*value="2026-09-15"')
        self.assertRegex(page, r'<input[^>]*name="accident_time"[^>]*value="07:30"')


class AnonymousReportJourneyTests(TestCase):
    """/anonymous-report/ -> formulaire -> enregistrement anonyme -> confirmation."""

    def setUp(self):
        self.url = reverse("reports:anonymous_create")

    def test_accessible_sans_connexion(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Signalement anonyme")
        self.assertContains(page, "Utiliser ma position actuelle")

    def test_enregistrement_anonyme(self):
        response = self.client.post(self.url, valid_report_data())
        self.assertEqual(response.status_code, 302)
        report = AccidentReport.objects.get()
        self.assertTrue(report.is_anonymous)
        self.assertIsNone(report.user)
        self.assertEqual(report.status, ReportStatus.PENDING)

    def test_confirmation_anonyme(self):
        page = follow_success(self.client.post(self.url, valid_report_data()))
        self.assertContains(page, "Votre signalement a été enregistré anonymement.")
        self.assertContains(page, "en attente de vérification")
        self.assertContains(page, "aucun lien n'est conservé")

    def test_utilisateur_connecte_reste_anonyme_sur_ce_parcours(self):
        user = User.objects.create_user("90123456")
        self.client.force_login(user)
        page = self.client.get(self.url)
        self.assertContains(page, "ne sera <strong>pas</strong> rattaché à votre compte")

        self.client.post(self.url, valid_report_data())
        report = AccidentReport.objects.get()
        self.assertTrue(report.is_anonymous)
        self.assertIsNone(report.user)
        self.assertEqual(user.reports.count(), 0)

    def test_aucun_lien_en_session_entre_compte_connecte_et_signalement_anonyme(self):
        self.client.force_login(User.objects.create_user("90123456"))
        self.client.post(self.url, valid_report_data())
        reference = AccidentReport.objects.get().reference
        self.assertNotIn(reference, json.dumps(dict(self.client.session), default=str))

    def test_les_champs_sensibles_ne_peuvent_pas_etre_falsifies(self):
        victim = User.objects.create_user("90999999")
        forged = valid_report_data(is_anonymous="", user=str(victim.pk), status="VERIFIED",
                                   is_demo="on", verified_by=str(victim.pk), reference="ACC-2000-000001")
        self.client.post(self.url, forged)
        report = AccidentReport.objects.get()
        self.assertTrue(report.is_anonymous)
        self.assertIsNone(report.user)
        self.assertEqual(report.status, ReportStatus.PENDING)
        self.assertFalse(report.is_demo)
        self.assertIsNone(report.verified_by)
        self.assertNotEqual(report.reference, "ACC-2000-000001")

    def test_aucune_identite_dans_la_confirmation(self):
        self.client.force_login(User.objects.create_user("90123456"))
        page = follow_success(self.client.post(self.url, valid_report_data()))
        # Seul le numéro MASQUÉ de la session apparaît dans la barre de navigation.
        self.assertNotContains(page, "90123456")
        self.assertNotContains(page, "+22890123456")
        main = page.content.decode().split('<main id="main"')[1].split("</main>")[0]
        self.assertNotIn("+228", main)  # rien d'identifiant dans le contenu de la confirmation


class SuccessPageTests(TestCase):
    def test_sans_jeton_retour_accueil(self):
        self.assertRedirects(self.client.get(reverse("reports:success")), reverse("home"))

    def test_jeton_falsifie_refuse(self):
        self.assertRedirects(self.client.get("/report/success/?t=abc:def:ghi"), reverse("home"))
        forged = signing.dumps({"ref": "ACC-2026-000001", "anon": False}, salt="autre.sel")
        self.assertRedirects(self.client.get(f"/report/success/?t={forged}"), reverse("home"))

    def test_jeton_expire(self):
        self.client.post(reverse("reports:anonymous_create"), valid_report_data())
        token = signing.dumps({"ref": "ACC-2026-000001", "anon": True}, salt="reports.success")
        with patch("reports.views.SUCCESS_MAX_AGE", -1):
            self.assertRedirects(self.client.get(f"/report/success/?t={token}"), reverse("home"))

    def test_actualiser_la_page_fonctionne(self):
        response = self.client.post(reverse("reports:anonymous_create"), valid_report_data())
        for _ in range(2):
            self.assertEqual(self.client.get(response["Location"]).status_code, 200)


class OutsideZoneJourneyTests(TestCase):
    URL = reverse("reports:anonymous_create")
    PARIS = dict(latitude="48.856600", longitude="2.352200")

    def test_avertissement_puis_confirmation(self):
        # 1) Hors zone sans confirmation : rien n'est enregistré, l'avertissement s'affiche.
        page = self.client.post(self.URL, valid_report_data(**self.PARIS))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(AccidentReport.objects.count(), 0)
        self.assertContains(page, "en dehors de la zone couverte par ACCIMAP")
        self.assertContains(page, "non officielle")
        self.assertNotContains(page, 'id="outside-zone-box" class="alert alert-warning mt-3 mb-0" role="alert" hidden')
        self.assertContains(page, 'value="48.856600"')  # coordonnées conservées dans le formulaire

        # 2) L'utilisateur confirme : le signalement est enregistré avec SES coordonnées.
        response = self.client.post(self.URL, valid_report_data(confirm_outside_zone="on", **self.PARIS))
        self.assertEqual(response.status_code, 302)
        report = AccidentReport.objects.get()
        self.assertEqual((report.location.y, report.location.x), (48.8566, 2.3522))

    def test_dans_la_zone_l_avertissement_reste_cache(self):
        page = self.client.get(self.URL)
        self.assertRegex(page.content.decode(), r'id="outside-zone-box"[^>]*\bhidden\b')

    def test_zone_officielle_configuree_est_utilisee_et_affichee(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_geojson(tmp, {"type": "Polygon", "coordinates": SQUARE})
            with override_settings(COVERAGE_ZONE_GEOJSON=path, COVERAGE_ZONE_IS_OFFICIAL=True,
                                   COVERAGE_ZONE_NAME="District Test"):
                page = self.client.get(self.URL)
                self.assertContains(page, "Zone de couverture : District Test")
                self.assertNotContains(page, "non officielle")
                # (6.20, 1.30) est dans le rectangle indicatif mais hors de ce polygone.
                refused = self.client.post(self.URL, valid_report_data(latitude="6.20", longitude="1.30"))
                self.assertEqual(refused.status_code, 200)
                self.assertEqual(AccidentReport.objects.count(), 0)


class PhotoJourneyTests(TestCase):
    def test_photo_enregistree_sans_metadonnees(self):
        upload = SimpleUploadedFile("IMG_0042_Jean_Dupont.jpg", make_jpeg_with_exif(), content_type="image/jpeg")
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            self.client.post(reverse("reports:anonymous_create"), {**valid_report_data(), "photo": upload})
            report = AccidentReport.objects.get()
            self.assertTrue(report.photo)
            self.assertRegex(report.photo.name, r"^reports/photos/\d{4}/\d{2}/[0-9a-f]{32}\.jpg$")
            self.assertNotIn("Dupont", report.photo.name)
            with report.photo.open("rb") as stored:
                data = stored.read()
            for marker in (b"Exif", b"Apple", b"iPhone", b"Dupont", b"GPS"):
                self.assertNotIn(marker, data)
            self.assertEqual(dict(Image.open(io.BytesIO(data)).getexif()), {})

    def test_fichier_dangereux_refuse_et_rien_n_est_cree(self):
        fake = SimpleUploadedFile("photo.jpg", b"<?php system('rm -rf /'); ?>", content_type="image/jpeg")
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            page = self.client.post(reverse("reports:anonymous_create"), {**valid_report_data(), "photo": fake})
            self.assertEqual(page.status_code, 200)
            self.assertEqual(AccidentReport.objects.count(), 0)
            self.assertEqual(list(__import__("pathlib").Path(tmp).rglob("*.*")), [])  # rien stocké


class CheckPositionEndpointTests(TestCase):
    URL = reverse("reports:check_position")

    def check(self, **params):
        return self.client.get(self.URL, params)

    def test_dans_la_zone(self):
        data = self.check(latitude="6.13", longitude="1.22").json()
        self.assertTrue(data["inside_zone"])
        self.assertFalse(data["is_official"])

    def test_hors_zone_n_est_pas_une_erreur(self):
        response = self.check(latitude="48.85", longitude="2.35")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["inside_zone"])
        self.assertIn("pas une limite administrative officielle", data["disclaimer"])

    def test_parametres_invalides(self):
        for params in [{}, {"latitude": "6.1"}, {"latitude": "abc", "longitude": "1"},
                       {"latitude": "95", "longitude": "1"}, {"latitude": "6", "longitude": "200"}]:
            with self.subTest(params=params):
                self.assertEqual(self.check(**params).status_code, 400)

    def test_get_uniquement(self):
        self.assertEqual(self.client.post(self.URL, {"latitude": "6", "longitude": "1"}).status_code, 405)

    def test_zone_officielle_sans_mention_indicative(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_geojson(tmp, {"type": "Polygon", "coordinates": SQUARE})
            with override_settings(COVERAGE_ZONE_GEOJSON=path, COVERAGE_ZONE_IS_OFFICIAL=True):
                data = self.check(latitude="6.12", longitude="1.22").json()
                self.assertTrue(data["inside_zone"])
                self.assertEqual(data["disclaimer"], "")
