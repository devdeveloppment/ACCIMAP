"""Django Admin (secours) : mêmes permissions que le tableau de bord, jamais de numéro de téléphone pour le staff."""
import tempfile
from pathlib import Path

from django.contrib.admin.models import CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import Point
from django.test import override_settings
from django.urls import reverse

from accounts.models import User
from accounts.roles import PERM_CHANGE, PERM_DELETE, PERM_VIEW, ROLE_STAFF, ROLE_SUPERUSER, get_role, set_role
from reports.models import AccidentReport
from reports.tests.helpers import make_image

from .helpers import PersonasTestCase, make_report, make_user

PARIS = Point(2.3522, 48.8566, srid=4326)
REPORTS = "admin:reports_accidentreport_"
USERS = "admin:accounts_user_"


def change_data(**overrides):
    data = {"status": "VERIFIED", "accident_type": "OTHER", "accident_date": "2026-09-01", "accident_time": "07:30",
            "severity": "LOW", "vehicle_count": "2", "injured_count": "1", "death_count": "0",
            "description": "Corrigé en admin", "location": "SRID=4326;POINT (1.2231 6.1319)", "admin_note": "note"}
    data.update(overrides)
    return data


def all_phone_forms(personas):
    return [form for user in personas.values() for form in (user.phone_number, user.phone_number[4:])]


class AdminAccessMatrixTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.report = make_report()
        self.target = make_user("90111222")

    def check(self, url, expected, method="get"):
        for persona, status in expected.items():
            with self.subTest(persona=persona, url=url):
                self.login(persona)
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, status, f"{persona} {method} {url}")
                if status == 302:
                    self.assertIn("/admin/login/", response["Location"])

    def test_page_d_accueil_de_l_admin(self):
        self.check(reverse("admin:index"), dict(anonymous=302, citizen=302, staff=200, staff_readonly=200,
                                                 staff_no_delete=200, staff_no_view=200, superuser=200))

    def test_signalements_consultation(self):
        view = dict(anonymous=302, citizen=302, staff_no_view=403, staff=200, staff_readonly=200,
                    staff_no_delete=200, superuser=200)
        self.check(reverse(REPORTS + "changelist"), view)
        self.check(reverse(REPORTS + "change", args=[self.report.pk]), view)
        self.check(reverse(REPORTS + "history", args=[self.report.pk]), view)

    def test_aucun_ajout_de_signalement_dans_l_admin(self):
        self.check(reverse(REPORTS + "add"), dict(anonymous=302, citizen=302, staff=403, staff_readonly=403,
                                                  staff_no_view=403, superuser=403))

    def test_suppression_selon_la_permission(self):
        self.check(reverse(REPORTS + "delete", args=[self.report.pk]), dict(
            anonymous=302, citizen=302, staff_no_view=403, staff_readonly=403, staff_no_delete=403,
            staff=200, superuser=200))

    def test_utilisateurs_superutilisateur_seulement(self):
        only_super = dict(anonymous=302, citizen=302, staff_no_view=403, staff=403, staff_readonly=403,
                          staff_no_delete=403, superuser=200)
        self.check(reverse(USERS + "changelist"), only_super)
        self.check(reverse(USERS + "change", args=[self.target.pk]), only_super)

    def test_ajout_et_suppression_d_utilisateur_impossibles_meme_pour_le_superutilisateur(self):
        self.login("superuser")
        self.assertEqual(self.client.get(reverse(USERS + "add")).status_code, 403)
        self.assertEqual(self.client.get(reverse(USERS + "delete", args=[self.target.pk])).status_code, 403)

    def test_journal_superutilisateur_seulement(self):
        self.check("/admin/admin/logentry/", dict(anonymous=302, citizen=302, staff=403, staff_readonly=403,
                                                  staff_no_view=403, superuser=200))

    def test_modification_refusee_sans_la_permission(self):
        url = reverse(REPORTS + "change", args=[self.report.pk])
        for persona in ("anonymous", "citizen", "staff_no_view", "staff_readonly"):
            with self.subTest(persona=persona):
                self.login(persona)
                self.client.post(url, change_data(description="PIRATÉ"))
                self.report.refresh_from_db()
                self.assertNotEqual(self.report.description, "PIRATÉ")

    def test_staff_avec_droits_modifie(self):
        self.login("staff")
        response = self.client.post(reverse(REPORTS + "change", args=[self.report.pk]), change_data())
        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual((self.report.description, self.report.accident_type), ("Corrigé en admin", "OTHER"))
        self.assertAlmostEqual(self.report.latitude, 6.1319)      # latitude/longitude dérivées du PointField

    def test_demotion_retire_l_acces_admin_immediatement(self):
        self.login("staff")
        url = reverse(REPORTS + "changelist")
        self.assertEqual(self.client.get(url).status_code, 200)
        set_role(self.personas["staff"], "user")
        self.assertEqual(self.client.get(url).status_code, 302)


class AdminReportContentTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("90123456")
        self.report = make_report(user=self.owner, status="PENDING", description="Carrefour du marché")
        self.anon = make_report(is_anonymous=True, status="VERIFIED", location=PARIS)
        self.login("staff")

    def test_aucun_numero_complet_dans_les_pages_admin(self):
        everything = all_phone_forms(self.personas) + self.phone_forms(self.owner)
        for url in [reverse("admin:index"), reverse(REPORTS + "changelist"),
                    reverse(REPORTS + "change", args=[self.report.pk]), reverse(REPORTS + "history", args=[self.report.pk])]:
            for persona in ("staff", "staff_readonly", "superuser"):
                with self.subTest(persona=persona, url=url):
                    self.login(persona)
                    body = self.client.get(url).content.decode()
                    for form in everything:
                        self.assertNotIn(form, body)

    def test_formulaire_sans_relation_utilisateur_ni_champ_sensible(self):
        page = self.client.get(reverse(REPORTS + "change", args=[self.report.pk]))
        html = page.content.decode()
        for forbidden in ('name="user"', 'name="verified_by"', 'name="photo"', 'name="is_anonymous"', 'name="reference"'):
            self.assertNotIn(forbidden, html)
        self.assertContains(page, "Identifié (+228****56)")
        self.assertContains(page, self.report.reference)

    def test_signalement_anonyme_sans_identite(self):
        page = self.client.get(reverse(REPORTS + "change", args=[self.anon.pk]))
        self.assertContains(page, "Anonyme (aucune identité enregistrée)")

    def test_colonnes_et_mode_dans_la_liste(self):
        page = self.client.get(reverse(REPORTS + "changelist"))
        for expected in ("Mode", "Dans la zone", "Gravité", "Statut"):
            self.assertContains(page, expected)

    def test_filtres_de_la_liste(self):
        url = reverse(REPORTS + "changelist")

        def found(**params):
            return {r.pk for r in self.client.get(url, params).context["cl"].result_list}

        self.assertEqual(found(status__exact="PENDING"), {self.report.pk})
        self.assertEqual(found(status__exact="VERIFIED"), {self.anon.pk})
        self.assertEqual(found(is_anonymous__exact="1"), {self.anon.pk})
        self.assertEqual(found(zone="outside"), {self.anon.pk})
        self.assertEqual(found(zone="inside"), {self.report.pk})
        self.assertEqual(found(accident_type__exact=self.report.accident_type), {self.report.pk, self.anon.pk})
        self.assertEqual(found(severity__exact="CRITICAL"), set())

    def test_recherche(self):
        url = reverse(REPORTS + "changelist")
        found = lambda q: {r.pk for r in self.client.get(url, {"q": q}).context["cl"].result_list}
        self.assertEqual(found("marché"), {self.report.pk})
        self.assertEqual(found(self.anon.reference), {self.anon.pk})
        self.assertEqual(found("introuvable"), set())
        # La recherche par numéro de téléphone n'existe pas : elle permettrait d'identifier un déclarant.
        self.assertEqual(found(self.owner.phone_number[4:]), set())

    def test_changement_de_statut_par_le_formulaire_enregistre_le_traitement(self):
        self.client.post(reverse(REPORTS + "change", args=[self.report.pk]), change_data(status="VERIFIED"))
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "VERIFIED")
        self.assertEqual(self.report.verified_by, self.personas["staff"])
        self.assertIsNotNone(self.report.verified_at)

    def test_le_formulaire_ne_peut_pas_changer_anonymat_ni_declarant(self):
        self.client.post(reverse(REPORTS + "change", args=[self.report.pk]),
                         change_data(is_anonymous="on", user="", verified_by=str(self.personas["staff"].pk), reference="X"))
        self.report.refresh_from_db()
        self.assertFalse(self.report.is_anonymous)
        self.assertEqual(self.report.user, self.owner)
        self.assertNotEqual(self.report.reference, "X")

    def test_modification_tracee(self):
        self.client.post(reverse(REPORTS + "change", args=[self.report.pk]), change_data())
        self.assertTrue(LogEntry.objects.filter(object_id=str(self.report.pk), action_flag=CHANGE).exists())

    def test_suppression_par_l_admin_efface_aussi_la_photo(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            report = make_report(photo=make_image())
            path = Path(report.photo.path)
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse(REPORTS + "delete", args=[report.pk]), {"post": "yes"})
            self.assertEqual(response.status_code, 302)
            self.assertFalse(AccidentReport.objects.filter(pk=report.pk).exists())
            self.assertFalse(path.exists())
            self.assertTrue(LogEntry.objects.filter(action_flag=DELETION).exists())

    def test_photo_via_la_vue_protegee_jamais_via_media(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            report = make_report(photo=make_image())
            page = self.client.get(reverse(REPORTS + "change", args=[report.pk]))
            self.assertContains(page, reverse("dashboard:report_photo", args=[report.pk]))
            self.assertNotContains(page, "/media/")

    def test_historique_avec_utilisateurs_masques(self):
        self.client.post(reverse("dashboard:report_review", args=[self.report.pk]), {"status": "VERIFIED", "admin_note": ""})
        self.login("superuser")
        page = self.client.get(reverse(REPORTS + "history", args=[self.report.pk]))
        self.assertContains(page, "+228****02")
        self.assertContains(page, "Statut : En attente → Vérifié")
        self.assertNotContains(page, self.personas["staff"].phone_number)


class AdminActionTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("90123456")
        self.a = make_report(status="PENDING", user=self.owner)
        self.b = make_report(status="PENDING", is_anonymous=True)
        self.url = reverse(REPORTS + "changelist")

    def act(self, action, *reports):
        return self.client.post(self.url, {"action": action, "_selected_action": [str(r.pk) for r in reports],
                                           "index": 0, "select_across": 0}, follow=True)

    def test_verifier_rejeter_remettre_en_attente(self):
        self.login("staff")
        self.act("mark_verified", self.a, self.b)
        for report in (self.a, self.b):
            report.refresh_from_db()
            self.assertEqual(report.status, "VERIFIED")
            self.assertEqual(report.verified_by, self.personas["staff"])
        self.act("mark_rejected", self.a)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, "REJECTED")
        self.act("mark_pending", self.a)
        self.a.refresh_from_db()
        self.assertEqual((self.a.status, self.a.verified_by), ("PENDING", None))

    def test_message_et_trace(self):
        self.login("staff")
        response = self.act("mark_verified", self.a, self.b)
        self.assertContains(response, "2 signalement(s) mis à jour.")
        entries = LogEntry.objects.filter(action_flag=CHANGE, change_message__contains="action groupée")
        self.assertEqual(entries.count(), 2)

    def test_les_actions_de_modification_exigent_la_permission(self):
        for persona in ("staff_readonly", "citizen", "anonymous"):
            with self.subTest(persona=persona):
                self.login(persona)
                self.act("mark_verified", self.a)
                self.a.refresh_from_db()
                self.assertEqual(self.a.status, "PENDING")

    def test_la_publication_sur_la_carte_publique_suit(self):
        self.login("staff")
        self.act("mark_verified", self.a)
        self.assertEqual(self.client.get(reverse("maps:public_data")).json()["meta"]["count"], 1)

    def test_export_de_la_selection_sans_numero_ni_donnee_privee(self):
        self.login("staff_readonly")           # la consultation suffit pour exporter
        response = self.act("export_selected_csv", self.a)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        body = response.content.decode("utf-8-sig")
        self.assertIn(self.a.reference, body)
        self.assertNotIn(self.b.reference, body)
        for form in self.phone_forms(self.owner):
            self.assertNotIn(form, body)
        self.assertTrue(LogEntry.objects.filter(change_message__startswith="[EXPORT]").exists())

    def test_suppression_groupee_exige_la_permission_de_suppression(self):
        self.login("staff_no_delete")
        self.client.post(self.url, {"action": "delete_selected", "_selected_action": [str(self.a.pk)], "post": "yes",
                                    "index": 0, "select_across": 0})
        self.assertTrue(AccidentReport.objects.filter(pk=self.a.pk).exists())


class AdminUserTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.target = make_user("90333333")
        self.url = reverse(USERS + "changelist")
        self.login("superuser")

    def act(self, action, *users):
        return self.client.post(self.url, {"action": action, "_selected_action": [str(u.pk) for u in users],
                                           "index": 0, "select_across": 0}, follow=True)

    def fresh(self, user):
        return User.objects.get(pk=user.pk)

    def test_liste_avec_numeros_complets_pour_le_superutilisateur(self):
        page = self.client.get(self.url)
        self.assertContains(page, "+22890333333")
        self.assertContains(page, "Superutilisateur")

    def test_recherche_et_filtres(self):
        found = lambda **p: {u.pk for u in self.client.get(self.url, p).context["cl"].result_list}
        self.assertEqual(found(q="333333"), {self.target.pk})
        self.assertIn(self.personas["staff"].pk, found(is_staff__exact="1"))
        self.assertNotIn(self.target.pk, found(is_staff__exact="1"))
        User.objects.filter(pk=self.target.pk).update(is_active=False)
        self.assertEqual(found(q="333333", is_active__exact="0"), {self.target.pk})

    def test_page_de_detail_en_lecture_seule_sauf_activation(self):
        page = self.client.get(reverse(USERS + "change", args=[self.target.pk]))
        self.assertContains(page, "Niveau d")                          # section « Niveau d'accès »
        self.assertRegex(page.content.decode(), r'<div class="readonly">Utilisateur</div>')
        html = page.content.decode()
        self.assertIn('name="is_active"', html)
        for name in ('name="is_staff"', 'name="is_superuser"', 'name="user_permissions"', 'name="phone_number"', 'name="password"'):
            self.assertNotIn(name, html)

    def test_activation_par_le_formulaire(self):
        url = reverse(USERS + "change", args=[self.target.pk])
        self.client.post(url, {})                       # case décochée : compte désactivé
        self.assertFalse(self.fresh(self.target).is_active)
        self.client.post(url, {"is_active": "on"})
        self.assertTrue(self.fresh(self.target).is_active)

    def test_ne_peut_pas_se_desactiver_soi_meme(self):
        me = self.personas["superuser"]
        response = self.client.post(reverse(USERS + "change", args=[me.pk]), {}, follow=True)
        self.assertTrue(self.fresh(me).is_active)
        self.assertContains(response, "ne pouvez pas désactiver votre propre compte")

    def test_changements_de_niveau_par_actions(self):
        self.act("make_staff", self.target)
        user = self.fresh(self.target)
        self.assertEqual(get_role(user), ROLE_STAFF)
        self.assertTrue(user.has_perm(PERM_VIEW) and user.has_perm(PERM_CHANGE) and user.has_perm(PERM_DELETE))
        self.act("make_staff_readonly", self.target)
        user = self.fresh(self.target)
        self.assertTrue(user.has_perm(PERM_VIEW))
        self.assertFalse(user.has_perm(PERM_CHANGE) or user.has_perm(PERM_DELETE))
        self.act("make_superuser", self.target)
        self.assertEqual(get_role(self.fresh(self.target)), ROLE_SUPERUSER)
        self.act("make_user", self.target)
        user = self.fresh(self.target)
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(user.user_permissions.count(), 0)

    def test_activation_et_desactivation_par_actions(self):
        self.act("deactivate", self.target)
        self.assertFalse(self.fresh(self.target).is_active)
        self.act("activate", self.target)
        self.assertTrue(self.fresh(self.target).is_active)

    def test_son_propre_compte_est_ignore_par_les_actions(self):
        me = self.personas["superuser"]
        response = self.act("make_user", me, self.target)
        self.assertEqual(get_role(self.fresh(me)), ROLE_SUPERUSER)
        self.assertContains(response, "propre compte a été ignoré")
        self.assertContains(response, "1 compte(s) mis à jour.")

    def test_actions_tracees_avec_numero_masque(self):
        self.act("make_staff", self.target)
        entry = LogEntry.objects.get(content_type=ContentType.objects.get_for_model(User), action_flag=CHANGE)
        self.assertEqual(entry.object_repr, "+228****33")
        self.assertIn("Utilisateur, actif → Staff, actif", entry.change_message)
        self.assertNotIn("90333333", entry.object_repr + entry.change_message)

    def test_le_staff_ne_peut_pas_executer_ces_actions(self):
        self.login("staff")
        self.act("make_superuser", self.target)
        self.assertFalse(self.fresh(self.target).is_staff)


class AdminLogEntryAndBrandingTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.report = make_report()
        self.login("staff")
        self.client.post(reverse("dashboard:report_review", args=[self.report.pk]), {"status": "VERIFIED", "admin_note": ""})
        self.client.get(reverse("exports:csv"))

    def test_journal_lisible_par_le_superutilisateur_avec_numeros_masques(self):
        self.login("superuser")
        page = self.client.get("/admin/admin/logentry/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "+228****02")
        self.assertContains(page, "Export")
        self.assertContains(page, "Statut : En attente → Vérifié")
        for form in all_phone_forms(self.personas):
            self.assertNotIn(form, page.content.decode())

    def test_journal_en_lecture_seule(self):
        self.login("superuser")
        entry = LogEntry.objects.first()
        self.assertEqual(self.client.get("/admin/admin/logentry/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/admin/logentry/{entry.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/admin/logentry/{entry.pk}/change/", {"change_message": "x"}).status_code, 403)
        self.assertTrue(LogEntry.objects.filter(pk=entry.pk).exists())

    def test_marque_et_retour_au_tableau_de_bord(self):
        page = self.client.get("/admin/")
        self.assertContains(page, "Administration ACCIMAP (secours)")
        self.assertContains(page, 'href="/dashboard/"')
        self.assertContains(page, "Retour au tableau de bord ACCIMAP")

    def test_le_numero_de_l_utilisateur_connecte_est_masque_dans_l_en_tete(self):
        page = self.client.get("/admin/")
        self.assertContains(page, "+228****02")
        self.assertNotContains(page, self.personas["staff"].phone_number)

    def test_le_staff_ne_voit_pas_les_modeles_sensibles_sur_l_accueil(self):
        page = self.client.get("/admin/").content.decode()
        self.assertIn("Signalements", page)
        self.assertNotIn("Utilisateurs", page)
        self.assertNotIn("Journal", page.replace("Journal de", ""))
