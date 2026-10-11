from django.test import TestCase
from django.urls import reverse

class PublicAccessTests(TestCase):
    def test_pages_publiques_ne_requierent_aucun_compte(self):
        for path in ("/", "/about/", "/contact/", "/map/", "/report/", "/anonymous-report/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_ancien_parcours_de_connexion_otp_est_supprime(self):
        for path in ("/login/", "/verify-otp/", "/resend-otp/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_espace_administrateur_reste_protege(self):
        response = self.client.get(reverse("dashboard:index"))
        self.assertRedirects(
            response,
            "/dashboard/login/?next=/dashboard/",
            fetch_redirect_response=False,
        )

    def test_un_utilisateur_simple_ne_peut_pas_ouvrir_le_tableau_de_bord(self):
        from accounts.models import User

        self.client.force_login(User.objects.create_user("90123456"))
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "droits nécessaires", status_code=403)

    def test_aucun_lien_d_administration_dans_l_interface_publique(self):
        page = self.client.get(reverse("home"))
        self.assertNotContains(page, "Tableau de bord")
        self.assertNotContains(page, "/dashboard/")

    def test_accueil_contient_les_actions_publiques(self):
        page = self.client.get(reverse("home"))
        self.assertContains(page, "Signaler un accident")
        self.assertContains(page, "Voir la carte")
