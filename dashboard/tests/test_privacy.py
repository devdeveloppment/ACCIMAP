"""Étanchéité : rien d'administratif ni de privé ne doit fuiter vers le public."""
import re

from django.urls import reverse

from accounts.models import User
from reports.tests.helpers import make_image

from .helpers import PersonasTestCase, make_report

PUBLIC_PAGES = ["home", "about", "maps:public_map", "reports:anonymous_create", "accounts:login"]
ADMIN_PAGES = ["dashboard:index", "dashboard:statistics", "dashboard:report_list", "dashboard:map",
               "dashboard:map_data", "exports:index"]


class PublicSurfaceTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("90123456")
        self.report = make_report(user=self.owner, status="VERIFIED", description="DESCRIPTION-PRIVEE",
                                  admin_note="NOTE-CONFIDENTIELLE")

    def test_aucune_page_publique_ne_pointe_vers_l_administration(self):
        for persona in ["anonymous", "citizen"]:
            self.login(persona)
            for name in PUBLIC_PAGES:
                with self.subTest(persona=persona, page=name):
                    html = self.client.get(reverse(name)).content.decode()
                    self.assertNotIn("/dashboard/", html)
                    self.assertNotIn("Tableau de bord", html)
                    for secret in ["DESCRIPTION-PRIVEE", "NOTE-CONFIDENTIELLE", "admin_note", "90123456"]:
                        self.assertNotIn(secret, html)

    def test_api_publique_sans_aucun_champ_administratif(self):
        self.login("anonymous")
        body = self.client.get(reverse("maps:public_data")).content.decode()
        for forbidden in ["in_zone", "reference", "anonymous", "status", "id", "NOTE-CONFIDENTIELLE",
                          "DESCRIPTION-PRIVEE", "90123456", "photo", "user"]:
            self.assertNotIn(f'"{forbidden}"', body, forbidden)
        self.assertNotIn("90123456", body)

    def test_les_parametres_d_administration_sont_ignores_par_l_api_publique(self):
        self.login("anonymous")
        base = self.client.get(reverse("maps:public_data")).json()
        for params in [{"q": "NOTE-CONFIDENTIELLE"}, {"status": "REJECTED"}, {"zone": "outside"}, {"mode": "anonymous"}]:
            self.assertEqual(self.client.get(reverse("maps:public_data"), params).json(), base, params)

    def test_endpoints_d_administration_fermes_au_public(self):
        for persona, expected in [("anonymous", {302, 401}), ("citizen", {403})]:
            self.login(persona)
            for name in ADMIN_PAGES:
                with self.subTest(persona=persona, page=name):
                    self.assertIn(self.client.get(reverse(name)).status_code, expected)
            for name in ["dashboard:user_list"]:
                self.assertIn(self.client.get(reverse(name)).status_code, expected)

    def test_aucune_page_publique_ne_sert_de_photo(self):
        self.login("anonymous")
        for name in PUBLIC_PAGES:
            self.assertNotIn("/photo/", self.client.get(reverse(name)).content.decode())


class PhoneNumberContainmentTests(PersonasTestCase):
    """Un numéro complet n'existe QUE dans l'espace de gestion des utilisateurs (superutilisateur)."""

    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("90123456")
        self.report = make_report(user=self.owner, status="PENDING")
        self.pages = [reverse(n) for n in ADMIN_PAGES] + [
            reverse("dashboard:report_detail", args=[self.report.pk]),
            reverse("dashboard:report_edit", args=[self.report.pk]),
            reverse("dashboard:report_delete", args=[self.report.pk]),
        ]

    def assert_no_full_phone(self, persona):
        self.login(persona)
        for url in self.pages:
            with self.subTest(persona=persona, url=url):
                body = self.client.get(url).content.decode()
                for user in self.personas.values():
                    pass  # les numéros des profils de test (y compris le sien) ne doivent pas non plus figurer
                self.assertNotRegex(body, r"\+228\d{8}")
                self.assertNotIn(self.owner.phone_number, body)
                self.assertNotIn("90123456", body)
                self.assertNotRegex(body, r"(?<![\d.])9\d{7}(?![\d.])")

    def test_pas_de_numero_complet_pour_le_staff(self):
        self.assert_no_full_phone("staff")

    def test_pas_de_numero_complet_pour_le_staff_lecture_seule(self):
        self.assert_no_full_phone("staff_readonly")

    def test_pas_de_numero_complet_meme_pour_le_superutilisateur_hors_gestion_des_utilisateurs(self):
        self.assert_no_full_phone("superuser")

    def test_le_numero_complet_n_existe_que_dans_la_gestion_des_utilisateurs(self):
        self.login("superuser")
        self.assertContains(self.client.get(reverse("dashboard:user_list")), "+22890123456")
        self.assertContains(self.client.get(reverse("dashboard:user_detail", args=[self.owner.pk])), "+22890123456")

    def test_la_carte_admin_n_expose_aucun_numero(self):
        self.login("superuser")
        body = self.client.get(reverse("dashboard:map_data")).content.decode()
        self.assertNotIn("90123456", body)
        self.assertNotIn("+228", body)


class AdminPageHardeningTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.report = make_report()
        self.login("superuser")

    def test_pages_privees_jamais_mises_en_cache(self):
        urls = [reverse(n) for n in ADMIN_PAGES + ["dashboard:user_list"]] + [
            reverse("dashboard:report_detail", args=[self.report.pk])]
        for url in urls:
            with self.subTest(url=url):
                self.assertIn("no-store", self.client.get(url)["Cache-Control"])

    def test_pages_html_non_indexees(self):
        for url in [reverse(n) for n in ADMIN_PAGES if n != "dashboard:map_data"] + [reverse("dashboard:user_list")]:
            with self.subTest(url=url):
                if url.endswith("/exports/"):
                    continue  # page provisoire (phase 7)
                self.assertContains(self.client.get(url), '<meta name="robots" content="noindex, nofollow">')

    def test_django_admin_ferme_aux_simples_utilisateurs(self):
        for persona in ["anonymous", "citizen"]:
            self.login(persona)
            response = self.client.get("/admin/")
            self.assertEqual(response.status_code, 302)
            self.assertIn("/admin/login/", response["Location"])
