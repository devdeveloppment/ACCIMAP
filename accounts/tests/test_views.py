import re

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import OTPCode, User
from accounts.sms import BaseSMSBackend

PHONE = "90123456"
FULL_PHONE = "+22890123456"
DEV = dict(OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=0)


class CapturingSMSBackend(BaseSMSBackend):
    sent = []

    def send(self, phone_number, message):
        CapturingSMSBackend.sent.append(message)


def start_login(client, phone=PHONE, **extra):
    return client.post(reverse("accounts:login"), {"phone_number": phone, **extra})


@override_settings(**DEV)
class LoginStepTests(TestCase):
    def test_page_de_connexion(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, "Recevoir mon code")
        self.assertContains(response, 'name="viewport"')  # mobile-first
        self.assertContains(response, 'type="tel"')

    def test_numero_invalide(self):
        response = start_login(self.client, "abc")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Numéro de téléphone invalide")
        self.assertEqual(OTPCode.objects.count(), 0)

    def test_numero_valide_redirige_vers_la_verification(self):
        response = start_login(self.client)
        self.assertRedirects(response, reverse("accounts:verify_otp"))
        self.assertEqual(self.client.session["otp_phone"], FULL_PHONE)
        self.assertEqual(OTPCode.objects.count(), 1)

    def test_utilisateur_deja_connecte_est_redirige(self):
        self.client.force_login(User.objects.create_user(PHONE))
        self.assertRedirects(self.client.get(reverse("accounts:login")), reverse("home"))

    def test_csrf_obligatoire(self):
        strict = Client(enforce_csrf_checks=True)
        response = strict.post(reverse("accounts:login"), {"phone_number": PHONE})
        self.assertEqual(response.status_code, 403)

    def test_double_demande_rapide_ne_bloque_pas_l_utilisateur(self):
        with override_settings(OTP_RESEND_DELAY_SECONDS=60):
            start_login(self.client)
            response = start_login(self.client)  # l'ancien code reste valable
            self.assertRedirects(response, reverse("accounts:verify_otp"))
            self.assertEqual(OTPCode.objects.count(), 1)

    @override_settings(OTP_MAX_REQUESTS_PER_HOUR=1)
    def test_plafond_horaire_affiche_une_erreur(self):
        start_login(self.client)
        response = start_login(self.client)
        self.assertContains(response, "Trop de codes demandés")


@override_settings(**DEV)
class VerifyStepTests(TestCase):
    def test_sans_numero_en_session_retour_a_la_connexion(self):
        response = self.client.get(reverse("accounts:verify_otp"))
        self.assertRedirects(response, reverse("accounts:login"))

    def test_mode_test_affiche_bandeau_et_code(self):
        start_login(self.client)
        code = self.client.session["otp_dev_code"]
        response = self.client.get(reverse("accounts:verify_otp"))
        self.assertContains(response, "MODE DÉVELOPPEMENT")
        self.assertContains(response, "SMS simulé")
        self.assertContains(response, code)
        self.assertContains(response, "+228****56")  # numéro masqué
        self.assertNotContains(response, FULL_PHONE)

    def test_code_incorrect(self):
        start_login(self.client)
        response = self.client.post(reverse("accounts:verify_otp"), {"code": "000000"})
        self.assertContains(response, "Le code OTP est incorrect.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_code_au_mauvais_format(self):
        start_login(self.client)
        response = self.client.post(reverse("accounts:verify_otp"), {"code": "12ab"})
        self.assertContains(response, "chiffres")

    def test_code_expire(self):
        from datetime import timedelta
        from django.utils import timezone

        start_login(self.client)
        code = self.client.session["otp_dev_code"]
        OTPCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.post(reverse("accounts:verify_otp"), {"code": code})
        self.assertContains(response, "Le code OTP a expiré")

    def test_parcours_complet_connexion_puis_deconnexion(self):
        start_login(self.client)
        code = self.client.session["otp_dev_code"]

        response = self.client.post(reverse("accounts:verify_otp"), {"code": code}, follow=True)
        self.assertRedirects(response, reverse("home"))
        self.assertContains(response, "Connexion réussie")
        self.assertContains(response, "Se déconnecter")
        self.assertContains(response, "+228****56")
        self.assertNotContains(response, FULL_PHONE)  # jamais le numéro complet
        user = User.objects.get(phone_number=FULL_PHONE)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        for key in ("otp_phone", "otp_next", "otp_dev_code"):
            self.assertNotIn(key, self.client.session)

        response = self.client.post(reverse("accounts:logout"), follow=True)
        self.assertRedirects(response, reverse("home"))
        self.assertContains(response, "Vous avez été déconnecté.")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Se connecter")

    def test_deconnexion_refuse_en_get(self):
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)

    def test_compte_desactive(self):
        User.objects.create_user(PHONE, is_active=False)
        start_login(self.client)
        code = self.client.session["otp_dev_code"]
        response = self.client.post(reverse("accounts:verify_otp"), {"code": code})
        self.assertContains(response, "désactivé")
        self.assertNotIn("_auth_user_id", self.client.session)


@override_settings(**DEV)
class NextRedirectTests(TestCase):
    def _login_with_next(self, next_url):
        start_login(self.client, next=next_url)
        code = self.client.session["otp_dev_code"]
        return self.client.post(reverse("accounts:verify_otp"), {"code": code})

    def test_acces_protege_puis_retour_a_la_page_demandee(self):
        response = self.client.get(reverse("reports:create"))
        self.assertRedirects(response, "/login/?next=/report/")
        response = self._login_with_next("/report/")
        self.assertRedirects(response, "/report/", fetch_redirect_response=False)

    def test_redirection_externe_ignoree(self):
        for evil in ["https://evil.example.com/", "//evil.example.com"]:
            with self.subTest(next=evil):
                self.client.logout()
                response = self._login_with_next(evil)
                self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)


@override_settings(**DEV)
class ResendTests(TestCase):
    def test_renvoi_genere_un_nouveau_code(self):
        start_login(self.client)
        response = self.client.post(reverse("accounts:resend_otp"))
        self.assertRedirects(response, reverse("accounts:verify_otp"))
        self.assertEqual(OTPCode.objects.filter(is_used=False).count(), 1)
        self.assertEqual(OTPCode.objects.count(), 2)

    def test_renvoi_trop_rapide_conserve_le_code(self):
        with override_settings(OTP_RESEND_DELAY_SECONDS=60):
            start_login(self.client)
            response = self.client.post(reverse("accounts:resend_otp"), follow=True)
            self.assertContains(response, "Veuillez patienter")
            self.assertEqual(OTPCode.objects.count(), 1)

    def test_renvoi_en_get_interdit(self):
        self.assertEqual(self.client.get(reverse("accounts:resend_otp")).status_code, 405)


@override_settings(OTP_DEV_MODE=False, OTP_RESEND_DELAY_SECONDS=0,
                   SMS_BACKEND="accounts.tests.test_views.CapturingSMSBackend")
class ProductionModeSecurityTests(TestCase):
    """OTP_DEV_MODE=False : le code ne doit apparaître NULLE PART côté navigateur."""

    def setUp(self):
        CapturingSMSBackend.sent = []

    def test_le_code_n_est_jamais_expose(self):
        response = start_login(self.client)
        self.assertRedirects(response, reverse("accounts:verify_otp"))
        code = re.search(r"\b(\d{6})\b", CapturingSMSBackend.sent[0]).group(1)  # reçu par SMS

        self.assertNotIn("otp_dev_code", self.client.session)
        page = self.client.get(reverse("accounts:verify_otp"))
        self.assertNotContains(page, "MODE DÉVELOPPEMENT")
        self.assertNotContains(page, "SMS simulé")
        self.assertNotContains(page, code)
        self.assertContains(page, "envoyé")

        # Le code reçu par SMS permet bien de se connecter.
        done = self.client.post(reverse("accounts:verify_otp"), {"code": code})
        self.assertRedirects(done, reverse("home"), fetch_redirect_response=False)

    def test_session_avec_code_residuel_n_affiche_rien(self):
        start_login(self.client)
        session = self.client.session
        session["otp_dev_code"] = "999999"  # même si une donnée résiduelle existait
        session.save()
        page = self.client.get(reverse("accounts:verify_otp"))
        self.assertNotContains(page, "999999")
        self.assertNotContains(page, "MODE DÉVELOPPEMENT")
        self.assertNotContains(page, "SMS simulé")

    @override_settings(SMS_BACKEND="accounts.sms.ConsoleSMSBackend")
    def test_sans_fournisseur_message_d_erreur_clair(self):
        response = start_login(self.client)
        self.assertContains(response, "pas pu être envoyé")  # l'apostrophe est échappée en HTML
        self.assertEqual(OTPCode.objects.count(), 0)


class AccessControlTests(TestCase):
    def setUp(self):
        self.citizen = User.objects.create_user(PHONE)
        self.admin = User.objects.create_superuser("90999999", "motdepasse-solide-42")

    def test_pages_publiques(self):
        for name in ["home", "about", "maps:public_map", "reports:anonymous_create", "accounts:login"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_signalement_anonyme_sans_connexion(self):
        self.assertEqual(self.client.get(reverse("reports:anonymous_create")).status_code, 200)

    def test_signalement_identifie_connecte_uniquement(self):
        self.assertEqual(self.client.get(reverse("reports:create")).status_code, 302)
        self.client.force_login(self.citizen)
        self.assertEqual(self.client.get(reverse("reports:create")).status_code, 200)

    def test_dashboard_visiteur_redirige_vers_connexion(self):
        response = self.client.get(reverse("dashboard:index"))
        # Vers la connexion PRIVÉE de l'administration, jamais vers la connexion citoyenne.
        self.assertRedirects(response, "/dashboard/login/?next=/dashboard/", fetch_redirect_response=False)

    def test_dashboard_interdit_a_un_simple_utilisateur(self):
        self.client.force_login(self.citizen)
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "droits nécessaires", status_code=403)

    def test_dashboard_autorise_a_un_administrateur_connecte_par_l_espace_prive(self):
        from dashboard.tests.helpers import open_admin_session
        open_admin_session(self.client, self.admin)
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)

    def test_un_administrateur_connecte_par_otp_doit_passer_par_l_espace_prive(self):
        self.client.force_login(self.admin)                    # session ouverte par la connexion citoyenne
        response = self.client.get(reverse("dashboard:index"))
        self.assertRedirects(response, "/dashboard/login/?next=/dashboard/", fetch_redirect_response=False)

    def test_aucun_lien_d_administration_dans_l_interface_publique(self):
        from dashboard.tests.helpers import open_admin_session
        for login in (lambda: self.client.force_login(self.citizen), lambda: self.client.force_login(self.admin),
                      lambda: open_admin_session(self.client, self.admin)):
            login()
            home = self.client.get(reverse("home")).content.decode()
            self.assertNotIn("Tableau de bord", home)
            self.assertNotIn("/dashboard/", home)

    def test_accueil_contient_les_actions_du_cahier_des_charges(self):
        page = self.client.get(reverse("home"))
        for expected in ["Signaler un accident", "Signaler anonymement", "Se connecter", "Voir la carte"]:
            self.assertContains(page, expected)

    def test_page_404_personnalisee(self):
        self.assertContains(self.client.get("/inexistant/"), "Page introuvable", status_code=404)
