"""
L'administration est un ESPACE PRIVÉ, séparé de l'interface citoyenne :
  * aucun lien ni élément d'administration dans l'interface publique, pour aucun profil ;
  * un citoyen ne peut atteindre AUCUNE URL administrative (pages, API, actions, Django Admin) ;
  * l'accès exige la connexion PROPRE à l'espace privé (identifiant + mot de passe) ; un compte staff connecté par
    OTP citoyen n'y a pas accès ;
  * les permissions existantes restent appliquées à l'intérieur de l'espace privé.
"""
import uuid

from django.contrib.admin.models import LogEntry
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from accounts.decorators import ADMIN_SESSION_KEY
from accounts.models import User
from accounts.roles import ROLE_STAFF, ROLE_USER, set_role
from reports.models import AccidentReport

from .helpers import PersonasTestCase, make_report, make_user, open_admin_session

PASSWORD = "Phrase-Secrete-Admin-2026"
PUBLIC_PAGES = ["home", "about", "maps:public_map", "reports:anonymous_create", "accounts:login"]


def admin_get_urls(report, user):
    return [
        reverse("dashboard:index"), reverse("dashboard:statistics"), reverse("dashboard:report_list"),
        reverse("dashboard:report_detail", args=[report.pk]), reverse("dashboard:report_photo", args=[report.pk]),
        reverse("dashboard:report_edit", args=[report.pk]), reverse("dashboard:report_delete", args=[report.pk]),
        reverse("dashboard:report_detail", args=[uuid.uuid4()]), reverse("dashboard:map"),
        reverse("dashboard:user_list"), reverse("dashboard:user_detail", args=[user.pk]),
        reverse("dashboard:password_change"), reverse("exports:index"), reverse("exports:csv"),
        reverse("exports:xlsx"), reverse("exports:pdf"),
    ]


def admin_post_urls(report, user):
    return [
        (reverse("dashboard:report_review", args=[report.pk]), {"status": "VERIFIED", "admin_note": "piraté"}),
        (reverse("dashboard:report_edit", args=[report.pk]), {"accident_type": "OTHER", "description": "piraté"}),
        (reverse("dashboard:report_delete", args=[report.pk]), {}),
        (reverse("dashboard:user_detail", args=[user.pk]), {"role": "superuser", "is_active": "on"}),
        (reverse("dashboard:user_set_password", args=[user.pk]), {"new_password1": PASSWORD, "new_password2": PASSWORD}),
        (reverse("dashboard:password_change"), {"old_password": "x", "new_password1": PASSWORD, "new_password2": PASSWORD}),
    ]


DJANGO_ADMIN_URLS = ["/admin/", "/admin/reports/accidentreport/", "/admin/accounts/user/", "/admin/admin/logentry/"]


class PublicInterfaceSeparationTests(PersonasTestCase):
    def test_aucun_element_d_administration_dans_l_interface_publique(self):
        for persona in ("anonymous", "citizen", "staff", "staff_readonly", "superuser"):
            self.login(persona)
            for name in PUBLIC_PAGES:
                with self.subTest(persona=persona, page=name):
                    html = self.client.get(reverse(name)).content.decode()
                    for forbidden in ("Tableau de bord", "/dashboard", "/admin/", "Administration avancée", "admin-header"):
                        self.assertNotIn(forbidden, html, forbidden)

    def test_formulaire_citoyen_sans_element_d_administration(self):
        self.login("citizen")
        html = self.client.get(reverse("reports:create")).content.decode()
        self.assertNotIn("/dashboard", html)
        self.assertNotIn("Tableau de bord", html)

    def test_espace_prive_avec_son_propre_gabarit(self):
        self.login("superuser")
        report = make_report()
        for url in [reverse("dashboard:index"), reverse("dashboard:report_list"), reverse("dashboard:statistics"),
                    reverse("dashboard:map"), reverse("exports:index"), reverse("dashboard:user_list"),
                    reverse("dashboard:report_detail", args=[report.pk]), reverse("dashboard:password_change")]:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('id="admin-header"', html)
                self.assertIn("Administration", html)
                self.assertIn('id="admin-logout"', html)
                self.assertIn('<meta name="robots" content="noindex, nofollow">', html)
                # Aucun élément de l'interface citoyenne :
                self.assertNotIn("accimap-navbar", html)
                self.assertNotIn("Signaler anonymement", html)
                self.assertNotIn('data-bs-toggle="dropdown"', html)

    def test_page_de_connexion_privee_autonome(self):
        page = self.client.get(reverse("dashboard:login"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        for expected in ("Espace d'administration", 'name="username"', 'name="password"', "Identifiant administrateur",
                         'autocomplete="current-password"', "Retour au site public", "noindex"):
            self.assertIn(expected, html)
        self.assertNotIn("accimap-navbar", html)
        self.assertNotIn("Signaler", html)


class CitizenNeverReachesAdministrationTests(PersonasTestCase):
    """Un citoyen, même connecté, ne peut atteindre aucune fonction administrative, par aucun moyen."""

    def setUp(self):
        super().setUp()
        self.target = make_user("90111222")
        self.report = make_report(status="PENDING", description="DONNEE-PRIVEE-ADMIN")

    def assert_nothing_leaked(self, response):
        body = response.content.decode(errors="ignore")
        self.assertNotIn(self.report.reference, body)
        self.assertNotIn("DONNEE-PRIVEE-ADMIN", body)
        self.assertNotIn("90111222", body)

    def test_citoyen_connecte_par_le_vrai_parcours_otp(self):
        client = Client()
        with override_settings(OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=0):
            client.post(reverse("accounts:login"), {"phone_number": "90555666"})
            client.post(reverse("accounts:verify_otp"), {"code": client.session["otp_dev_code"]})
        self.assertIn("_auth_user_id", client.session)
        for url in admin_get_urls(self.report, self.target):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assert_nothing_leaked(response)
        api = client.get(reverse("dashboard:map_data"))
        self.assertEqual(api.status_code, 403)
        self.assert_nothing_leaked(api)

    def test_citoyen_actions_post_refusees_sans_effet(self):
        self.login("citizen")
        for url, data in admin_post_urls(self.report, self.target):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, data).status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual((self.report.status, self.report.admin_note), ("PENDING", ""))
        self.assertTrue(AccidentReport.objects.filter(pk=self.report.pk).exists())
        target = User.objects.get(pk=self.target.pk)
        self.assertFalse(target.is_staff or target.is_superuser or target.has_usable_password())

    def test_citoyen_jamais_dans_le_django_admin(self):
        self.login("citizen")
        for url in DJANGO_ADMIN_URLS:
            with self.subTest(url=url):
                response = self.client.get(url, follow=True)
                self.assertEqual(response.redirect_chain[-1][0].split("?")[0], reverse("dashboard:login"))
                self.assert_nothing_leaked(response)

    def test_une_session_citoyenne_falsifiee_reste_refusee(self):
        self.login("citizen")
        session = self.client.session
        session[ADMIN_SESSION_KEY] = self.personas["citizen"].pk       # tentative de forcer la marque administrateur
        session.save()
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 403)
        self.assertEqual(self.client.get(reverse("exports:csv")).status_code, 403)

    def test_un_citoyen_ne_peut_pas_ouvrir_l_espace_prive(self):
        cache.clear()
        citizen = self.personas["citizen"]
        for password in ("", "123456", PASSWORD):
            response = self.client.post(reverse("dashboard:login"), {"username": citizen.phone_number, "password": password})
            self.assertNotIn("_auth_user_id", self.client.session)
        citizen.set_password(PASSWORD)          # même avec un mot de passe, un compte non staff est refusé
        citizen.save()
        cache.clear()
        response = self.client.post(reverse("dashboard:login"), {"username": citizen.phone_number, "password": PASSWORD})
        self.assertContains(response, "Identifiant ou mot de passe incorrect")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_visiteur_toujours_renvoye_vers_la_connexion_privee(self):
        self.client.logout()
        for url in admin_get_urls(self.report, self.target):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/dashboard/login/?next="), response["Location"])
        self.assertEqual(self.client.get(reverse("dashboard:map_data")).status_code, 401)


class StaffConnectedByOtpTests(PersonasTestCase):
    """Un compte staff connecté par la connexion CITOYENNE n'a pas accès : il doit passer par l'espace privé."""

    def setUp(self):
        super().setUp()
        self.report = make_report(status="PENDING")
        self.client.force_login(self.personas["superuser"])            # session OTP, sans marque administrateur

    def test_pages_renvoient_vers_la_connexion_privee(self):
        for url in admin_get_urls(self.report, self.personas["citizen"]):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/dashboard/login/", response["Location"])

    def test_api_et_django_admin(self):
        self.assertEqual(self.client.get(reverse("dashboard:map_data")).status_code, 401)
        response = self.client.get("/admin/", follow=True)
        self.assertEqual(response.redirect_chain[-1][0].split("?")[0], reverse("dashboard:login"))

    def test_actions_refusees_sans_effet(self):
        self.client.post(reverse("dashboard:report_review", args=[self.report.pk]), {"status": "VERIFIED"})
        self.client.post(reverse("dashboard:report_delete", args=[self.report.pk]))
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "PENDING")

    def test_la_marque_administrateur_est_liee_a_l_utilisateur(self):
        session = self.client.session
        session[ADMIN_SESSION_KEY] = self.personas["staff"].pk          # marque d'un AUTRE utilisateur
        session.save()
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 302)

    def test_apres_connexion_privee_l_acces_est_ouvert(self):
        open_admin_session(self.client, self.personas["superuser"])
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 200)


class AdminLoginTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.staff = self.personas["staff"]
        self.staff.set_password(PASSWORD)
        self.staff.save()
        self.client.logout()

    def login_post(self, username, password, **extra):
        return self.client.post(reverse("dashboard:login"), {"username": username, "password": password, **extra})

    def test_connexion_reussie_quel_que_soit_le_format_du_numero(self):
        for username in ("90000002", "90 00 00 02", "+228 90 00 00 02", "0022890000002"):
            with self.subTest(username=username):
                self.client.logout()
                response = self.login_post(username, PASSWORD)
                self.assertRedirects(response, reverse("dashboard:index"), fetch_redirect_response=False)
                self.assertEqual(self.client.session[ADMIN_SESSION_KEY], self.staff.pk)

    def test_message_d_erreur_unique_quel_que_soit_le_motif(self):
        inactive = make_user("90000077", ROLE_STAFF)
        inactive.set_password(PASSWORD); inactive.is_active = False; inactive.save()
        no_password = make_user("90000078", ROLE_STAFF)                 # staff sans mot de passe défini
        cases = [("90000002", "mauvais"), ("99999999", PASSWORD), ("90000077", PASSWORD),
                 ("90000078", PASSWORD), ("pas-un-numero", PASSWORD)]
        for username, password in cases:
            with self.subTest(username=username):
                cache.clear()
                response = self.login_post(username, password)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Identifiant ou mot de passe incorrect, ou compte sans accès à l&#x27;administration.")
                self.assertNotIn(ADMIN_SESSION_KEY, self.client.session)

    def test_champs_vides_simple_validation_de_saisie(self):
        response = self.login_post("90000002", "")
        self.assertContains(response, "Ce champ est obligatoire")
        self.assertNotIn(ADMIN_SESSION_KEY, self.client.session)

    def test_le_blocage_s_affiche_sans_erreur_serveur(self):
        with override_settings(ADMIN_LOGIN_MAX_ATTEMPTS=1):
            self.login_post("90000002", "mauvais")
            response = self.login_post("90000002", "mauvais")
        self.assertEqual(response.status_code, 200)                     # jamais une erreur 500
        self.assertContains(response, "Trop de tentatives")
        self.assertContains(response, 'name="username"')                # le formulaire reste affiché

    def test_mot_de_passe_jamais_renvoye(self):
        response = self.login_post("90000002", "mauvais-mot-de-passe-xyz")
        self.assertNotContains(response, "mauvais-mot-de-passe-xyz")

    def test_redirection_vers_next_interne_seulement(self):
        response = self.login_post("90000002", PASSWORD, next="/dashboard/statistics/")
        self.assertRedirects(response, "/dashboard/statistics/", fetch_redirect_response=False)
        self.client.logout()
        response = self.login_post("90000002", PASSWORD, next="https://evil.example.com/")
        self.assertRedirects(response, reverse("dashboard:index"), fetch_redirect_response=False)

    def test_deja_connecte_renvoye_au_tableau_de_bord(self):
        self.login_post("90000002", PASSWORD)
        self.assertRedirects(self.client.get(reverse("dashboard:login")), reverse("dashboard:index"), fetch_redirect_response=False)

    @override_settings(ADMIN_SESSION_AGE=1800)
    def test_session_administrateur_limitee_dans_le_temps(self):
        self.login_post("90000002", PASSWORD)
        self.assertEqual(self.client.session.get_expiry_age(), 1800)

    @override_settings(ADMIN_LOGIN_MAX_ATTEMPTS=3)
    def test_blocage_apres_trop_d_echecs_meme_avec_le_bon_mot_de_passe(self):
        for _ in range(3):
            self.login_post("90000002", "mauvais")
        response = self.login_post("+228 90 00 00 02", PASSWORD)             # autre format, même compte
        self.assertContains(response, "Trop de tentatives")
        self.assertNotIn(ADMIN_SESSION_KEY, self.client.session)
        other = make_user("90000088", ROLE_STAFF)
        other.set_password(PASSWORD); other.save()
        self.assertEqual(self.login_post("90000088", PASSWORD).status_code, 302)   # un autre compte n'est pas bloqué
        cache.clear()
        self.client.logout()
        self.assertEqual(self.login_post("90000002", PASSWORD).status_code, 302)   # après le délai de blocage

    def test_une_reussite_remet_le_compteur_a_zero(self):
        with override_settings(ADMIN_LOGIN_MAX_ATTEMPTS=3):
            self.login_post("90000002", "mauvais"); self.login_post("90000002", "mauvais")
            self.login_post("90000002", PASSWORD)
            self.client.logout()
            self.login_post("90000002", "mauvais"); self.login_post("90000002", "mauvais")
            self.assertEqual(self.login_post("90000002", PASSWORD).status_code, 302)

    def test_csrf_obligatoire(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse("dashboard:login"), {"username": "90000002", "password": PASSWORD}).status_code, 403)

    def test_deconnexion(self):
        self.login_post("90000002", PASSWORD)
        self.assertEqual(self.client.get(reverse("dashboard:logout")).status_code, 405)
        response = self.client.post(reverse("dashboard:logout"), follow=True)
        self.assertRedirects(response, reverse("dashboard:login"))
        self.assertContains(response, "déconnecté de l&#x27;espace d&#x27;administration")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 302)

    def test_django_admin_meme_point_d_entree(self):
        response = self.client.get("/admin/login/?next=/admin/")
        self.assertRedirects(response, "/dashboard/login/?next=/admin/", fetch_redirect_response=False)
        self.login_post("90000002", PASSWORD, next="/admin/")
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertEqual(self.client.get("/admin/logout/").status_code, 405)
        self.client.post("/admin/logout/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_les_permissions_restent_appliquees_dans_l_espace_prive(self):
        readonly = self.personas["staff_readonly"]
        readonly.set_password(PASSWORD); readonly.save()
        self.login_post("90000003", PASSWORD)
        report = make_report()
        self.assertEqual(self.client.get(reverse("dashboard:report_detail", args=[report.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:report_edit", args=[report.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:user_list")).status_code, 403)


class AdminPasswordManagementTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.target = make_user("90333333", ROLE_STAFF)
        self.url = reverse("dashboard:user_set_password", args=[self.target.pk])
        self.login("superuser")

    def set_password(self, p1, p2=None):
        return self.client.post(self.url, {"new_password1": p1, "new_password2": p2 if p2 is not None else p1}, follow=True)

    def test_le_superutilisateur_definit_le_mot_de_passe_d_un_staff(self):
        detail = self.client.get(reverse("dashboard:user_detail", args=[self.target.pk]))
        self.assertContains(detail, "Aucun mot de passe")
        response = self.set_password(PASSWORD)
        self.assertContains(response, "Le mot de passe d&#x27;administration a été défini.")
        self.assertTrue(User.objects.get(pk=self.target.pk).check_password(PASSWORD))
        self.assertContains(self.client.get(reverse("dashboard:user_detail", args=[self.target.pk])), "Un mot de passe est défini")
        entry = LogEntry.objects.get(change_message="Mot de passe d'administration défini.")
        self.assertEqual(entry.object_repr, "+228****33")
        self.assertNotIn(PASSWORD, entry.change_message + entry.object_repr)

    def test_regles_de_robustesse_appliquees(self):
        for weak in ("court", "12345678", "password", "90333333"):
            with self.subTest(weak=weak):
                self.set_password(weak)
                self.assertFalse(User.objects.get(pk=self.target.pk).has_usable_password())
        self.set_password(PASSWORD, "different-Mot-2026")
        self.assertFalse(User.objects.get(pk=self.target.pk).has_usable_password())

    def test_jamais_pour_un_citoyen(self):
        citizen = self.personas["citizen"]
        response = self.client.post(reverse("dashboard:user_set_password", args=[citizen.pk]),
                                    {"new_password1": PASSWORD, "new_password2": PASSWORD}, follow=True)
        self.assertContains(response, "n&#x27;a pas de mot de passe")
        self.assertFalse(User.objects.get(pk=citizen.pk).has_usable_password())

    def test_son_propre_mot_de_passe_passe_par_la_page_dediee(self):
        me = self.personas["superuser"]
        response = self.client.post(reverse("dashboard:user_set_password", args=[me.pk]), {"new_password1": PASSWORD, "new_password2": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:password_change"), fetch_redirect_response=False)

    def test_reserve_au_superutilisateur(self):
        self.login("staff")
        self.assertEqual(self.client.post(self.url, {"new_password1": PASSWORD, "new_password2": PASSWORD}).status_code, 403)
        self.assertFalse(User.objects.get(pk=self.target.pk).has_usable_password())

    def test_changer_son_propre_mot_de_passe_garde_la_session(self):
        me = self.personas["superuser"]
        old = "motdepasse-solide-42"                     # défini par make_user/create_superuser dans les profils
        me.set_password(old); me.save()
        self.login("superuser")
        bad = self.client.post(reverse("dashboard:password_change"), {"old_password": "faux", "new_password1": PASSWORD, "new_password2": PASSWORD})
        self.assertEqual(bad.status_code, 200)
        ok = self.client.post(reverse("dashboard:password_change"), {"old_password": old, "new_password1": PASSWORD, "new_password2": PASSWORD})
        self.assertRedirects(ok, reverse("dashboard:index"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)       # session conservée
        self.assertTrue(User.objects.get(pk=me.pk).check_password(PASSWORD))

    def test_retrogradation_retire_le_mot_de_passe(self):
        self.set_password(PASSWORD)
        self.client.post(reverse("dashboard:user_detail", args=[self.target.pk]), {"role": "user", "is_active": "on"})
        target = User.objects.get(pk=self.target.pk)
        self.assertFalse(target.has_usable_password())
        self.client.logout()
        response = self.client.post(reverse("dashboard:login"), {"username": "90333333", "password": PASSWORD})
        self.assertContains(response, "Identifiant ou mot de passe incorrect")

    def test_set_role_utilisateur_retire_le_mot_de_passe(self):
        self.target.set_password(PASSWORD); self.target.save()
        set_role(self.target, ROLE_USER)
        self.assertFalse(User.objects.get(pk=self.target.pk).has_usable_password())
        set_role(self.target, ROLE_STAFF)
        self.assertFalse(User.objects.get(pk=self.target.pk).has_usable_password())   # nouveau mot de passe à définir
