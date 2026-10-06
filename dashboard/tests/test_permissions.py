"""
Matrice de permissions : visiteur, utilisateur simple, staff (plusieurs variantes), superutilisateur.

Chaque ligne indique le statut HTTP attendu pour chaque profil. Les actions refusées doivent
en plus ne RIEN modifier en base.
"""
import uuid

from django.contrib.admin.models import LogEntry
from django.urls import reverse

from accounts.models import User
from reports.choices import ReportStatus
from reports.models import AccidentReport

from .helpers import PersonasTestCase, make_report

LOGIN = 302  # redirection vers la connexion (visiteur)


class PermissionMatrixTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.report = make_report(status=ReportStatus.PENDING, description="Texte d'origine")
        self.pk = self.report.pk
        self.target = User.objects.create_user("90111222")  # compte géré par le superutilisateur

    # profils : anonymous, citizen, staff, staff_readonly, staff_no_delete, staff_no_view, superuser
    def expected(self, **kw):
        base = dict(anonymous=LOGIN, citizen=403, staff_no_view=403)
        base.update(kw)
        return base

    def check(self, url, expected, method="get", data=None):
        for persona, status in expected.items():
            with self.subTest(persona=persona, url=url, method=method):
                self.login(persona)
                response = getattr(self.client, method)(url, data or {})
                self.assertEqual(response.status_code, status, f"{persona} {method.upper()} {url}")
                if status == LOGIN:
                    self.assertIn("/login/", response["Location"])

    # ------------------------------------------------------------------ consultation (permission « voir »)
    def test_pages_de_consultation(self):
        voir = self.expected(staff=200, staff_readonly=200, staff_no_delete=200, superuser=200)
        for name in ["index", "statistics", "report_list", "map", ]:
            self.check(reverse(f"dashboard:{name}"), voir)
        self.check(reverse("dashboard:report_detail", args=[self.pk]), voir)
        self.check(reverse("exports:index"), voir)

    def test_donnees_de_la_carte_admin_api(self):
        self.check(reverse("dashboard:map_data"), dict(
            anonymous=401, citizen=403, staff_no_view=403, staff=200, staff_readonly=200,
            staff_no_delete=200, superuser=200))

    def test_photo(self):
        # Pas de photo : 404 pour qui a le droit de voir ; refus pour les autres.
        self.check(reverse("dashboard:report_photo", args=[self.pk]), self.expected(
            staff=404, staff_readonly=404, staff_no_delete=404, superuser=404))

    # ------------------------------------------------------------------ modification (permission « modifier »)
    def test_modification_get(self):
        self.check(reverse("dashboard:report_edit", args=[self.pk]), self.expected(
            staff=200, staff_readonly=403, staff_no_delete=200, superuser=200))

    def test_changement_de_statut_post(self):
        url = reverse("dashboard:report_review", args=[self.pk])
        for persona, allowed in [("anonymous", False), ("citizen", False), ("staff_no_view", False),
                                 ("staff_readonly", False), ("staff", True), ("staff_no_delete", True),
                                 ("superuser", True)]:
            with self.subTest(persona=persona):
                AccidentReport.objects.filter(pk=self.pk).update(status=ReportStatus.PENDING, admin_note="")
                self.login(persona)
                response = self.client.post(url, {"status": "VERIFIED", "admin_note": "ok"})
                self.report.refresh_from_db()
                if allowed:
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(self.report.status, ReportStatus.VERIFIED)
                else:
                    self.assertIn(response.status_code, (302, 403))
                    if persona != "anonymous":
                        self.assertEqual(response.status_code, 403)
                    self.assertEqual(self.report.status, ReportStatus.PENDING)  # RIEN n'a changé
                    self.assertEqual(self.report.admin_note, "")

    def test_modification_post_refusee_ne_change_rien(self):
        url = reverse("dashboard:report_edit", args=[self.pk])
        data = {"accident_type": "OTHER", "accident_date": "2026-01-02", "accident_time": "10:00",
                "severity": "LOW", "vehicle_count": 9, "injured_count": 9, "death_count": 9,
                "description": "PIRATÉ"}
        for persona in ["anonymous", "citizen", "staff_no_view", "staff_readonly"]:
            with self.subTest(persona=persona):
                self.login(persona)
                self.client.post(url, data)
                self.report.refresh_from_db()
                self.assertEqual(self.report.description, "Texte d'origine")
                self.assertNotEqual(self.report.injured_count, 9)

    def test_modification_post_autorisee(self):
        self.login("staff")
        data = {"accident_type": "OTHER", "accident_date": "2026-01-02", "accident_time": "10:00",
                "severity": "LOW", "vehicle_count": 3, "injured_count": 4, "death_count": 0,
                "description": "Corrigé"}
        self.assertEqual(self.client.post(reverse("dashboard:report_edit", args=[self.pk]), data).status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.description, "Corrigé")

    # ------------------------------------------------------------------ suppression (permission « supprimer »)
    def test_suppression_get_confirmation(self):
        self.check(reverse("dashboard:report_delete", args=[self.pk]), self.expected(
            staff=200, staff_readonly=403, staff_no_delete=403, superuser=200))

    def test_suppression_post(self):
        url = reverse("dashboard:report_delete", args=[self.pk])
        for persona in ["anonymous", "citizen", "staff_no_view", "staff_readonly", "staff_no_delete"]:
            with self.subTest(persona=persona):
                self.login(persona)
                self.client.post(url)
                self.assertTrue(AccidentReport.objects.filter(pk=self.pk).exists(), f"{persona} a pu supprimer !")
        self.login("staff")
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(AccidentReport.objects.filter(pk=self.pk).exists())

    def test_superutilisateur_peut_aussi_supprimer(self):
        self.login("superuser")
        self.client.post(reverse("dashboard:report_delete", args=[self.pk]))
        self.assertFalse(AccidentReport.objects.filter(pk=self.pk).exists())

    # ------------------------------------------------------------------ gestion des utilisateurs (superutilisateur seul)
    def test_gestion_des_utilisateurs_reservee_au_superutilisateur(self):
        seul_super = self.expected(superuser=200, staff=403, staff_readonly=403, staff_no_delete=403)
        self.check(reverse("dashboard:user_list"), seul_super)
        self.check(reverse("dashboard:user_detail", args=[self.target.pk]), seul_super)

    def test_modification_des_acces_refusee_aux_non_superutilisateurs(self):
        url = reverse("dashboard:user_detail", args=[self.target.pk])
        data = {"role": "superuser", "is_active": "on"}
        for persona in ["anonymous", "citizen", "staff_no_view", "staff", "staff_readonly", "staff_no_delete"]:
            with self.subTest(persona=persona):
                self.login(persona)
                self.client.post(url, data)
                target = User.objects.get(pk=self.target.pk)
                self.assertFalse(target.is_staff or target.is_superuser, f"{persona} a promu un compte !")

    def test_le_superutilisateur_peut_promouvoir(self):
        self.login("superuser")
        response = self.client.post(reverse("dashboard:user_detail", args=[self.target.pk]),
                                    {"role": "staff", "is_active": "on", "can_change": "on", "can_delete": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(pk=self.target.pk).is_staff)

    # ------------------------------------------------------------------ cohérence générale
    def test_aucun_acces_ne_depend_de_l_existence_de_l_objet(self):
        """Un profil non autorisé reçoit 403 même pour un identifiant inexistant (pas de fuite d'existence)."""
        unknown = uuid.uuid4()
        for persona in ["citizen", "staff_no_view"]:
            self.login(persona)
            for name in ["report_detail", "report_photo", "report_edit", "report_delete"]:
                self.assertEqual(self.client.get(reverse(f"dashboard:{name}", args=[unknown])).status_code, 403, name)
        self.login("staff")
        self.assertEqual(self.client.get(reverse("dashboard:report_detail", args=[unknown])).status_code, 404)

    def test_les_actions_en_post_uniquement_refusent_les_get(self):
        self.login("staff")
        self.assertEqual(self.client.get(reverse("dashboard:report_review", args=[self.pk])).status_code, 405)
        self.login("citizen")
        self.assertEqual(self.client.get(reverse("dashboard:report_review", args=[self.pk])).status_code, 403)

    def test_csrf_obligatoire_sur_les_actions(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.personas["superuser"])
        for url, data in [(reverse("dashboard:report_review", args=[self.pk]), {"status": "VERIFIED"}),
                          (reverse("dashboard:report_delete", args=[self.pk]), {}),
                          (reverse("dashboard:user_detail", args=[self.target.pk]), {"role": "staff"})]:
            self.assertEqual(strict.post(url, data).status_code, 403, url)
        self.assertTrue(AccidentReport.objects.filter(pk=self.pk).exists())
        self.assertEqual(AccidentReport.objects.get(pk=self.pk).status, ReportStatus.PENDING)


class LivePermissionChangeTests(PersonasTestCase):
    """Un changement de droits ou de compte doit s'appliquer immédiatement."""

    def setUp(self):
        super().setUp()
        self.report = make_report()

    def test_staff_retrograde_perd_l_acces_immediatement(self):
        self.login("staff")
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)
        from accounts.roles import ROLE_USER, set_role
        set_role(self.personas["staff"], ROLE_USER)
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 403)

    def test_retrait_du_droit_de_modifier(self):
        from accounts.roles import ROLE_STAFF, set_role
        self.login("staff")
        edit = reverse("dashboard:report_edit", args=[self.report.pk])
        self.assertEqual(self.client.get(edit).status_code, 200)
        set_role(self.personas["staff"], ROLE_STAFF, can_change=False)
        self.assertEqual(self.client.get(edit).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:report_detail", args=[self.report.pk])).status_code, 200)

    def test_compte_desactive_est_deconnecte_immediatement(self):
        self.login("staff")
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)
        User.objects.filter(pk=self.personas["staff"].pk).update(is_active=False)
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_liens_de_navigation_selon_le_profil(self):
        # Interface publique : AUCUN lien d'administration, pour aucun profil (même administrateur).
        # Espace privé : l'onglet « Utilisateurs » n'existe que pour le superutilisateur.
        expectations = {"anonymous": None, "citizen": None, "staff_no_view": None,
                        "staff": False, "staff_readonly": False, "superuser": True}
        for persona, sees_users in expectations.items():
            with self.subTest(persona=persona):
                self.login(persona)
                home = self.client.get(reverse("home")).content.decode()
                self.assertNotIn("Tableau de bord", home)
                self.assertNotIn("/dashboard/", home)
                if sees_users is not None:
                    page = self.client.get(reverse("dashboard:index")).content.decode()
                    self.assertEqual('href="/dashboard/users/"' in page, sees_users)

    def test_actions_masquees_selon_les_permissions(self):
        url = reverse("dashboard:report_detail", args=[self.report.pk])
        cases = {  # (formulaire de statut, lien modifier, lien supprimer)
            "staff": (True, True, True), "staff_no_delete": (True, True, False),
            "staff_readonly": (False, False, False), "superuser": (True, True, True),
        }
        for persona, (review, edit, delete) in cases.items():
            with self.subTest(persona=persona):
                self.login(persona)
                page = self.client.get(url).content.decode()
                self.assertEqual('id="review-form"' in page, review)
                self.assertEqual('id="edit-link"' in page, edit)
                self.assertEqual('id="delete-link"' in page, delete)
