from django.contrib.admin.models import CHANGE, LogEntry
from django.urls import reverse

from accounts.models import User
from accounts.otp import AccountDisabled, request_otp, verify_otp
from accounts.roles import PERM_CHANGE, PERM_DELETE, PERM_VIEW, ROLE_STAFF, ROLE_SUPERUSER, ROLE_USER, get_role, set_role
from django.test import override_settings

from .helpers import PersonasTestCase, make_report, make_user


def fresh(user):
    return User.objects.get(pk=user.pk)


class UserListTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("90111111")
        self.bob = make_user("90222222")
        self.bob.is_active = False
        self.bob.save()
        for _ in range(2):
            make_report(user=self.alice)
        make_report(is_anonymous=True)  # anonyme : jamais rattaché à un compte
        self.login("superuser")
        self.url = reverse("dashboard:user_list")

    def users(self, **params):
        return [i["obj"].phone_number for i in self.client.get(self.url, params).context["users"]]

    def test_numeros_complets_visibles_du_superutilisateur(self):
        page = self.client.get(self.url)
        self.assertContains(page, "+22890111111")
        self.assertContains(page, "+22890222222")

    def test_recherche_par_numero(self):
        self.assertEqual(self.users(q="111111"), ["+22890111111"])
        self.assertEqual(self.users(q="90 22 22 22"), ["+22890222222"])
        self.assertEqual(self.users(q="+228 90 11 11 11"), ["+22890111111"])
        self.assertEqual(self.users(q="pas-un-numero"), [])

    def test_filtre_niveau_d_acces(self):
        self.assertCountEqual(self.users(role="superuser"), ["+22890000006"])
        self.assertIn("+22890000002", self.users(role="staff"))
        self.assertNotIn("+22890000006", self.users(role="staff"))
        self.assertIn("+22890111111", self.users(role="user"))
        self.assertNotIn("+22890000002", self.users(role="user"))

    def test_filtre_compte_actif(self):
        self.assertEqual(self.users(q="22222", active="0"), ["+22890222222"])
        self.assertEqual(self.users(q="22222", active="1"), [])

    def test_compteur_de_signalements_identifies_seulement(self):
        counts = {i["obj"].phone_number: i["obj"].report_count for i in self.client.get(self.url).context["users"]}
        self.assertEqual(counts["+22890111111"], 2)
        self.assertEqual(counts["+22890222222"], 0)
        self.assertEqual(sum(counts.values()), 2)  # le signalement anonyme n'est compté pour personne

    def test_pagination(self):
        for i in range(30):
            make_user(f"9100{i:04d}")
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["users"]), 25)
        self.assertEqual(len(self.client.get(self.url, {"page": 2}).context["users"]), response.context["page"].paginator.count - 25)

    def test_aucun_lien_entre_signalement_anonyme_et_utilisateur(self):
        page = self.client.get(reverse("dashboard:user_detail", args=[self.alice.pk]))
        self.assertContains(page, "Signalements identifiés")


class UserAccessTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.target = make_user("90333333")
        self.url = reverse("dashboard:user_detail", args=[self.target.pk])
        self.login("superuser")

    def post(self, **data):
        return self.client.post(self.url, data, follow=True)

    def test_page_de_gestion(self):
        page = self.client.get(self.url)
        self.assertContains(page, "+22890333333")
        self.assertContains(page, "Superutilisateur")
        self.assertContains(page, 'id="staff-permissions"')

    def test_promotion_en_staff_avec_droits_choisis(self):
        response = self.post(role="staff", is_active="on", can_change="on")
        self.assertContains(response, "ont été mis à jour")
        user = fresh(self.target)
        self.assertEqual(get_role(user), ROLE_STAFF)
        self.assertTrue(user.has_perm(PERM_VIEW) and user.has_perm(PERM_CHANGE))
        self.assertFalse(user.has_perm(PERM_DELETE))

    def test_promotion_en_superutilisateur(self):
        self.post(role="superuser", is_active="on")
        self.assertEqual(get_role(fresh(self.target)), ROLE_SUPERUSER)

    def test_retrogradation(self):
        set_role(self.target, ROLE_STAFF)
        self.post(role="user", is_active="on", can_change="on", can_delete="on")
        user = fresh(self.target)
        self.assertEqual(get_role(user), ROLE_USER)
        self.assertEqual(user.user_permissions.count(), 0)

    def test_droits_detailles_ignores_pour_un_non_staff(self):
        self.post(role="user", is_active="on", can_change="on", can_delete="on")
        user = fresh(self.target)
        self.assertFalse(user.has_perm(PERM_CHANGE) or user.has_perm(PERM_DELETE))

    def test_desactivation_et_reactivation(self):
        self.post(role="user")                      # is_active absent = désactivé
        self.assertFalse(fresh(self.target).is_active)
        self.post(role="user", is_active="on")
        self.assertTrue(fresh(self.target).is_active)

    def test_desactiver_puis_reactiver_un_staff_conserve_ses_droits(self):
        """Régression : le formulaire d'un compte désactivé affichait des droits vides, qui étaient effacés."""
        set_role(self.target, ROLE_STAFF, can_change=True, can_delete=False)
        self.post(role="staff", can_change="on")                      # désactivation (is_active absent)
        self.assertFalse(fresh(self.target).is_active)
        page = self.client.get(self.url)                              # le formulaire doit refléter les vrais droits
        self.assertTrue(page.context["form"].initial["can_change"])
        self.assertFalse(page.context["form"].initial["can_delete"])
        self.assertContains(page, 'name="can_change" class="form-check-input" id="id_can_change" checked')
        # Réactivation avec les valeurs proposées par le formulaire : rien ne doit changer.
        self.post(role="staff", is_active="on", can_change="on")
        user = fresh(self.target)
        self.assertTrue(user.is_active)
        self.assertTrue(user.has_perm(PERM_VIEW) and user.has_perm(PERM_CHANGE))
        self.assertFalse(user.has_perm(PERM_DELETE))

    def test_compte_desactive_ne_peut_plus_se_connecter(self):
        self.post(role="user")
        with override_settings(OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=0):
            result = request_otp("90333333")
            with self.assertRaises(AccountDisabled):
                verify_otp("90333333", result.dev_code)

    def test_staff_promu_doit_recevoir_un_mot_de_passe_puis_se_connecter_par_l_espace_prive(self):
        self.post(role="staff", is_active="on", can_change="on", can_delete="on")
        self.client.logout()
        # 1) Connecté par OTP citoyen : aucun accès, renvoi vers la connexion privée.
        self.client.force_login(fresh(self.target))
        self.assertRedirects(self.client.get(reverse("dashboard:index")), "/dashboard/login/?next=/dashboard/",
                             fetch_redirect_response=False)
        # 2) Sans mot de passe défini, la connexion privée échoue.
        self.client.logout()
        failed = self.client.post(reverse("dashboard:login"), {"username": "90333333", "password": "Nouveau-Mot-De-Passe-2026"})
        self.assertContains(failed, "Identifiant ou mot de passe incorrect")
        # 3) Le superutilisateur définit le mot de passe ; la connexion privée réussit.
        self.login("superuser")
        self.client.post(reverse("dashboard:user_set_password", args=[self.target.pk]),
                         {"new_password1": "Nouveau-Mot-De-Passe-2026", "new_password2": "Nouveau-Mot-De-Passe-2026"})
        self.client.logout()
        ok = self.client.post(reverse("dashboard:login"), {"username": "90 33 33 33", "password": "Nouveau-Mot-De-Passe-2026"})
        self.assertRedirects(ok, reverse("dashboard:index"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:user_list")).status_code, 403)   # staff, pas superutilisateur

    def test_role_invalide_refuse(self):
        response = self.post(role="dieu", is_active="on")
        self.assertContains(response, "Sélectionnez un choix valide")
        self.assertEqual(get_role(fresh(self.target)), ROLE_USER)

    def test_utilisateur_inexistant(self):
        self.assertEqual(self.client.get(reverse("dashboard:user_detail", args=[999999])).status_code, 404)

    def test_modification_tracee_sans_numero_complet(self):
        self.post(role="staff", is_active="on", can_change="on")
        entry = LogEntry.objects.get(action_flag=CHANGE)
        self.assertEqual(entry.user, self.personas["superuser"])
        self.assertIn("Utilisateur, actif → Staff, actif", entry.change_message)
        self.assertEqual(entry.object_repr, "+228****33")
        self.assertNotIn("90333333", entry.object_repr + entry.change_message)


class SelfProtectionTests(PersonasTestCase):
    """Un superutilisateur ne peut pas se verrouiller hors de l'administration."""

    def setUp(self):
        super().setUp()
        self.me = self.personas["superuser"]
        self.url = reverse("dashboard:user_detail", args=[self.me.pk])
        self.login("superuser")

    def test_la_page_de_son_propre_compte_n_a_pas_de_formulaire(self):
        page = self.client.get(self.url)
        self.assertContains(page, "propre compte")
        self.assertNotContains(page, 'id="access-form"')

    def test_ne_peut_pas_se_desactiver_ni_se_retrograder_meme_par_requete_forgee(self):
        for data in [{"role": "superuser"}, {"role": "staff", "is_active": "on"}, {"role": "user", "is_active": "on"}]:
            with self.subTest(data=data):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                me = fresh(self.me)
                self.assertTrue(me.is_active and me.is_superuser and me.is_staff)

    def test_peut_en_revanche_gerer_un_autre_superutilisateur(self):
        other = make_user("90444444", ROLE_SUPERUSER)
        self.client.post(reverse("dashboard:user_detail", args=[other.pk]), {"role": "staff", "is_active": "on", "can_change": "on"})
        self.assertEqual(get_role(fresh(other)), ROLE_STAFF)
        self.assertTrue(fresh(self.me).is_superuser)
