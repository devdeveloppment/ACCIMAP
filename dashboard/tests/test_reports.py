import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.contrib.admin.models import CHANGE, DELETION, LogEntry
from django.contrib.gis.geos import Point
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from reports.choices import ReportStatus
from reports.models import AccidentReport
from reports.tests.helpers import make_image

from .helpers import PersonasTestCase, make_report

PARIS = Point(2.3522, 48.8566, srid=4326)
LIST = "dashboard:report_list"


def references(response):
    return [r.reference for r in response.context["page"]]


class ReportListTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.login("staff")
        today = timezone.localdate()
        self.owner = User.objects.create_user("90123456")
        self.pending = make_report(status="PENDING", accident_type="RUN_OFF_ROAD", severity="LOW",
                                   accident_date=today - timedelta(days=30), description="Carrefour du marché")
        self.verified = make_report(status="VERIFIED", accident_type="ROLLOVER", severity="SEVERE",
                                    accident_date=today - timedelta(days=10), user=self.owner,
                                    admin_note="Confirmé par la police")
        self.rejected = make_report(status="REJECTED", accident_type="OTHER", severity="CRITICAL",
                                    accident_date=today - timedelta(days=2), is_anonymous=True, location=PARIS)

    def get(self, **params):
        return self.client.get(reverse(LIST), params)

    def test_tous_les_statuts_sont_visibles_du_staff(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(references(response), [self.pending.reference, self.verified.reference, self.rejected.reference])

    def test_filtre_statut(self):
        for status, report in [("PENDING", self.pending), ("VERIFIED", self.verified), ("REJECTED", self.rejected)]:
            self.assertEqual(references(self.get(status=status)), [report.reference])

    def test_filtre_mode_identifie_anonyme(self):
        self.assertEqual(references(self.get(mode="anonymous")), [self.rejected.reference])
        self.assertCountEqual(references(self.get(mode="identified")), [self.pending.reference, self.verified.reference])

    def test_filtre_periode(self):
        today = timezone.localdate()
        self.assertEqual(references(self.get(date_from=(today - timedelta(days=5)).isoformat())), [self.rejected.reference])
        self.assertEqual(references(self.get(date_to=(today - timedelta(days=20)).isoformat())), [self.pending.reference])

    def test_filtre_type_et_gravite(self):
        self.assertEqual(references(self.get(accident_type="ROLLOVER")), [self.verified.reference])
        self.assertEqual(references(self.get(severity="CRITICAL")), [self.rejected.reference])

    def test_filtre_zone_calcule_dynamiquement(self):
        self.assertEqual(references(self.get(zone="outside")), [self.rejected.reference])
        self.assertCountEqual(references(self.get(zone="inside")), [self.pending.reference, self.verified.reference])

    def test_zone_suit_la_configuration_sans_rien_stocker(self):
        from reports.tests.test_zone import SQUARE, write_geojson
        # Avec un autre polygone, les MÊMES signalements changent de côté : rien n'est stocké.
        with tempfile.TemporaryDirectory() as tmp, override_settings(COVERAGE_ZONE_GEOJSON=write_geojson(tmp, {"type": "Polygon", "coordinates": SQUARE})):
            self.assertEqual(references(self.get(zone="inside")), [])   # le carré 6.10-6.15 n'en contient aucun
            self.assertEqual(len(references(self.get(zone="outside"))), 3)
            self.assertContains(self.get(), "Hors zone")
        # Retour à la zone par défaut : le classement initial revient immédiatement.
        self.assertEqual(references(self.get(zone="outside")), [self.rejected.reference])
        self.assertEqual(len(references(self.get(zone="inside"))), 2)

    def test_recherche_reference_description_note(self):
        self.assertEqual(references(self.get(q=self.pending.reference)), [self.pending.reference])
        self.assertEqual(references(self.get(q="marché")), [self.pending.reference])
        self.assertEqual(references(self.get(q="police")), [self.verified.reference])
        self.assertEqual(references(self.get(q="introuvable")), [])

    def test_combinaison_de_filtres(self):
        self.assertEqual(references(self.get(status="VERIFIED", mode="identified", accident_type="ROLLOVER", zone="inside")),
                         [self.verified.reference])
        self.assertEqual(references(self.get(status="VERIFIED", mode="anonymous")), [])

    def test_filtres_invalides_affichent_une_erreur_et_aucun_resultat(self):
        response = self.get(status="N_IMPORTE_QUOI")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filtres invalides")
        self.assertEqual(references(response), [])
        response = self.get(date_from="2026-05-10", date_to="2026-05-01")
        self.assertContains(response, "postérieure")

    def test_tri(self):
        newest = references(self.get(sort="-accident"))
        self.assertEqual(newest[0], self.rejected.reference)
        self.assertEqual(references(self.get(sort="accident"))[0], self.pending.reference)
        self.assertEqual(len(references(self.get(sort="valeur-inconnue"))), 3)  # repli sûr

    def test_mode_et_zone_affiches(self):
        page = self.get().content.decode()
        self.assertIn("Anonyme", page)
        self.assertIn("Hors zone", page)
        self.assertIn("Dans la zone", page)

    def test_pagination_et_conservation_des_filtres(self):
        for _ in range(23):
            make_report(status="VERIFIED")
        response = self.get(status="VERIFIED")
        self.assertEqual(len(references(response)), 20)
        self.assertEqual(response.context["page"].paginator.count, 24)
        self.assertContains(response, "status=VERIFIED&amp;page=2")
        page2 = self.get(status="VERIFIED", page=2)
        self.assertEqual(len(references(page2)), 4)
        self.assertEqual(len(references(self.get(page="abc"))), 20)  # page invalide -> page 1

    def test_aucun_numero_de_telephone_dans_la_liste(self):
        page = self.get().content.decode()
        for form in self.phone_forms(self.owner):
            self.assertNotIn(form, page)
        # Seul le numéro MASQUÉ du staff connecté figure dans la barre de navigation.
        self.assertNotRegex(page, r"\+228\d{8}")
        self.assertNotRegex(page, r"(?<!\d)9\d{7}(?!\d)")


class ReportDetailTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("90123456")
        self.identified = make_report(user=self.owner, description="Ligne 1\nLigne 2", location=Point(1.2231, 6.1319, srid=4326))
        self.anonymous = make_report(is_anonymous=True, location=PARIS)

    def get(self, report, persona="staff"):
        self.login(persona)
        return self.client.get(reverse("dashboard:report_detail", args=[report.pk]))

    def test_informations_completes(self):
        page = self.get(self.identified)
        for expected in [self.identified.reference, "Collision", "Gravité estimée", "Ligne 1",
                         "Identifié", "6.131900", "1.223100", "Dans la zone"]:
            self.assertContains(page, expected)
        self.assertContains(page, 'id="detail-map"')

    def test_decimale_avec_point_pour_les_coordonnees(self):
        self.assertNotContains(self.get(self.identified), "6,131900")

    def test_signalement_anonyme(self):
        page = self.get(self.anonymous)
        self.assertContains(page, "aucune identité enregistrée")
        self.assertContains(page, "Hors zone")
        self.assertContains(page, "48.856600")

    def test_declarant_masque_pour_le_staff(self):
        page = self.get(self.identified)
        self.assertContains(page, "+228****56")
        for form in self.phone_forms(self.owner):
            self.assertNotContains(page, form)
        self.assertNotContains(page, "Voir le compte")

    def test_superutilisateur_a_un_lien_vers_le_compte_mais_pas_le_numero_complet(self):
        page = self.get(self.identified, "superuser")
        self.assertContains(page, "Voir le compte")
        self.assertContains(page, reverse("dashboard:user_detail", args=[self.owner.pk]))
        for form in self.phone_forms(self.owner):
            self.assertNotContains(page, form)

    def test_zone_presentee_comme_indicative(self):
        page = self.get(self.identified).content.decode()
        self.assertIn("non officielle", page)

    def test_lecture_seule_pour_le_staff_sans_droit_de_modifier(self):
        self.identified.admin_note = "Note existante"
        self.identified.save()
        page = self.get(self.identified, "staff_readonly")
        self.assertContains(page, "Note existante")
        self.assertContains(page, "pas le modifier")
        self.assertNotContains(page, 'id="review-form"')

    def test_inconnu_donne_404_pour_un_staff(self):
        import uuid
        self.login("staff")
        self.assertEqual(self.client.get(reverse("dashboard:report_detail", args=[uuid.uuid4()])).status_code, 404)

    def test_pages_privees_non_mises_en_cache_et_non_indexees(self):
        page = self.get(self.identified)
        self.assertIn("no-store", page["Cache-Control"])
        self.assertContains(page, '<meta name="robots" content="noindex, nofollow">')


class ReviewTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.report = make_report(status="PENDING")
        self.url = reverse("dashboard:report_review", args=[self.report.pk])
        self.login("staff")

    def post(self, status, note=""):
        return self.client.post(self.url, {"status": status, "admin_note": note}, follow=True)

    def test_verifier_enregistre_qui_quand_et_la_note(self):
        response = self.post("VERIFIED", "Confirmé par la police")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.VERIFIED)
        self.assertEqual(self.report.verified_by, self.personas["staff"])
        self.assertIsNotNone(self.report.verified_at)
        self.assertEqual(self.report.admin_note, "Confirmé par la police")
        self.assertContains(response, "Statut mis à jour : Vérifié.")

    def test_le_signalement_verifie_apparait_sur_la_carte_publique(self):
        self.assertEqual(self.client.get(reverse("maps:public_data")).json()["meta"]["count"], 0)
        self.post("VERIFIED")
        self.assertEqual(self.client.get(reverse("maps:public_data")).json()["meta"]["count"], 1)

    def test_rejeter_et_remettre_en_attente(self):
        self.post("VERIFIED")
        self.post("REJECTED", "Doublon")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.REJECTED)
        self.assertEqual(self.client.get(reverse("maps:public_data")).json()["meta"]["count"], 0)
        self.post("PENDING")
        self.report.refresh_from_db()
        self.assertIsNone(self.report.verified_by)
        self.assertIsNone(self.report.verified_at)

    def test_note_seule_ne_change_ni_statut_ni_verificateur(self):
        self.post("VERIFIED")
        self.report.refresh_from_db()
        verified_at = self.report.verified_at
        self.login("superuser")
        response = self.post("VERIFIED", "Précision ajoutée")
        self.report.refresh_from_db()
        self.assertEqual(self.report.admin_note, "Précision ajoutée")
        self.assertEqual(self.report.verified_by, self.personas["staff"])  # inchangé
        self.assertEqual(self.report.verified_at, verified_at)
        self.assertContains(response, "Note enregistrée.")

    def test_statut_invalide_refuse(self):
        response = self.post("SUPPRIME")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.PENDING)
        self.assertContains(response, "Action invalide")

    def test_note_trop_longue_refusee(self):
        self.post("VERIFIED", "x" * 2001)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.PENDING)

    def test_action_tracee_dans_l_historique(self):
        self.post("VERIFIED")
        entry = LogEntry.objects.get(action_flag=CHANGE)
        self.assertEqual(entry.user, self.personas["staff"])
        self.assertEqual(entry.object_id, str(self.report.pk))
        self.assertIn("En attente → Vérifié", entry.change_message)


class EditTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("90123456")
        self.report = make_report(user=self.owner, status="VERIFIED", location=Point(1.2231, 6.1319, srid=4326))
        self.url = reverse("dashboard:report_edit", args=[self.report.pk])
        self.login("staff")
        self.data = {"accident_type": "ROLLOVER", "accident_date": "2026-09-01", "accident_time": "07:30",
                     "severity": "CRITICAL", "vehicle_count": "2", "injured_count": "5", "death_count": "1",
                     "description": "Description corrigée"}

    def test_formulaire_prerempli(self):
        page = self.client.get(self.url)
        self.assertContains(page, self.report.reference)
        self.assertContains(page, 'value="%s"' % self.report.accident_date.isoformat())

    def test_modification_valide(self):
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual((self.report.accident_type, self.report.severity, self.report.injured_count), ("ROLLOVER", "CRITICAL", 5))
        self.assertEqual(self.report.description, "Description corrigée")
        self.assertEqual(self.report.accident_date, date(2026, 9, 1))

    def test_position_statut_anonymat_et_declarant_inchanges(self):
        before = AccidentReport.objects.get(pk=self.report.pk)
        forged = {**self.data, "status": "REJECTED", "is_anonymous": "on", "user": "", "latitude": "10", "longitude": "10",
                  "reference": "ACC-0000-000000", "is_demo": "on", "location": "POINT(0 0)"}
        self.client.post(self.url, forged)
        after = AccidentReport.objects.get(pk=self.report.pk)
        self.assertEqual(after.status, before.status)
        self.assertEqual(after.is_anonymous, before.is_anonymous)
        self.assertEqual(after.user, self.owner)
        self.assertEqual(after.reference, before.reference)
        self.assertEqual((after.location.x, after.location.y), (before.location.x, before.location.y))
        self.assertEqual((after.latitude, after.longitude), (before.latitude, before.longitude))
        self.assertFalse(after.is_demo)

    def test_donnees_invalides_refusees(self):
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        for override in [{"accident_date": future}, {"accident_type": ""}, {"injured_count": "-1"},
                         {"vehicle_count": "101"}, {"description": "x" * 2001}]:
            with self.subTest(override=override):
                response = self.client.post(self.url, {**self.data, **override})
                self.assertEqual(response.status_code, 200)
        self.report.refresh_from_db()
        self.assertNotEqual(self.report.description, "Description corrigée")

    def test_modification_tracee(self):
        self.client.post(self.url, self.data)
        entry = LogEntry.objects.get(action_flag=CHANGE)
        self.assertIn("accident_type", entry.change_message)

    def test_suppression_de_la_photo(self):
        from reports.models import ReportPhoto

        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            report = make_report(photo=make_image())
            extra = ReportPhoto.objects.create(report=report, image=make_image("extra.png"))
            path, extra_path = Path(report.photo.path), Path(extra.image.path)
            self.assertTrue(path.exists() and extra_path.exists())
            url = reverse("dashboard:report_edit", args=[report.pk])
            self.assertContains(self.client.get(url), "Supprimer les photos")
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {**self.data, "remove_photo": "on"})
            report.refresh_from_db()
            self.assertFalse(report.photo)
            self.assertFalse(path.exists())
            # Les photos supplémentaires sont aussi supprimées (option de confidentialité).
            self.assertEqual(report.extra_photos.count(), 0)
            self.assertFalse(extra_path.exists())

    def test_option_photo_absente_sans_photo(self):
        self.assertNotContains(self.client.get(self.url), "Supprimer les photos")


class DeleteTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self.report = make_report()
        self.url = reverse("dashboard:report_delete", args=[self.report.pk])
        self.login("staff")

    def test_confirmation_obligatoire_get_ne_supprime_pas(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Supprimer ce signalement ?")
        self.assertContains(page, "définitive")
        self.assertContains(page, self.report.reference)
        self.assertTrue(AccidentReport.objects.filter(pk=self.report.pk).exists())

    def test_suppression_apres_confirmation(self):
        response = self.client.post(self.url, follow=True)
        self.assertFalse(AccidentReport.objects.filter(pk=self.report.pk).exists())
        self.assertRedirects(response, reverse(LIST))
        self.assertContains(response, f"Le signalement {self.report.reference} a été supprimé")

    def test_suppression_tracee_sans_donnee_personnelle(self):
        self.client.post(self.url)
        entry = LogEntry.objects.get(action_flag=DELETION)
        self.assertEqual(entry.user, self.personas["staff"])
        self.assertEqual(entry.object_repr, self.report.reference)

    def test_la_photo_est_supprimee_du_stockage(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            report = make_report(photo=make_image())
            path = Path(report.photo.path)
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("dashboard:report_delete", args=[report.pk]))
            self.assertFalse(path.exists())

    def test_inconnu(self):
        import uuid
        self.assertEqual(self.client.get(reverse("dashboard:report_delete", args=[uuid.uuid4()])).status_code, 404)


class PhotoAccessTests(PersonasTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._override = override_settings(MEDIA_ROOT=self._tmp.name)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.report = make_report(photo=make_image("photo.png", "PNG"))
        self.url = reverse("dashboard:report_photo", args=[self.report.pk])

    def test_staff_recoit_l_image(self):
        self.login("staff")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))

    def test_refus_aux_profils_non_autorises(self):
        self.login("anonymous")
        self.assertEqual(self.client.get(self.url).status_code, 302)
        for persona in ["citizen", "staff_no_view"]:
            self.login(persona)
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_pas_de_route_media_publique(self):
        """Même avec l'URL exacte du fichier, /media/ n'est servi à personne, pas même au staff."""
        direct = "/" + self.report.photo.url.lstrip("/")
        for persona in ["anonymous", "citizen", "staff", "superuser"]:
            self.login(persona)
            self.assertEqual(self.client.get(direct).status_code, 404, persona)

    def test_fichier_manquant_donne_404(self):
        Path(self.report.photo.path).unlink()
        self.login("staff")
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_affichee_dans_la_page_de_detail_via_la_vue_protegee(self):
        self.login("staff")
        page = self.client.get(reverse("dashboard:report_detail", args=[self.report.pk]))
        self.assertContains(page, f'src="{self.url}"')
        self.assertNotContains(page, "/media/")

    def test_jamais_dans_les_donnees_publiques(self):
        self.report.set_status("VERIFIED", self.personas["staff"])
        body = self.client.get(reverse("maps:public_data")).content.decode()
        self.assertNotIn("photo", body)
        self.assertNotIn(Path(self.report.photo.name).name, body)


class AdminMapTests(PersonasTestCase):
    KEYS = {"id", "reference", "status", "status_label", "type_label", "severity", "severity_label",
            "date", "time", "injured", "deaths", "anonymous", "in_zone"}

    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("90123456")
        self.pending = make_report(status="PENDING", injured_count=1)
        self.verified = make_report(status="VERIFIED", injured_count=2, user=self.owner, description="DESC-SECRETE")
        self.rejected = make_report(status="REJECTED", injured_count=3, location=PARIS, is_anonymous=True)
        self.login("staff")

    def fetch(self, **params):
        response = self.client.get(reverse("dashboard:map_data"), params)
        return response, json.loads(response.content)

    def test_tous_les_statuts_sont_presents(self):
        response, data = self.fetch()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(f["properties"]["status"] for f in data["features"]), ["PENDING", "REJECTED", "VERIFIED"])

    def test_champs_exposes_et_aucune_donnee_privee(self):
        response, data = self.fetch()
        for feature in data["features"]:
            self.assertEqual(set(feature["properties"]), self.KEYS)
        body = response.content.decode()
        for forbidden in ["90123456", "+228", "DESC-SECRETE", "photo", "admin_note", "phone", "user"]:
            self.assertNotIn(forbidden, body)

    def test_zone_calculee_dynamiquement(self):
        _, data = self.fetch()
        by_status = {f["properties"]["status"]: f["properties"] for f in data["features"]}
        self.assertTrue(by_status["VERIFIED"]["in_zone"])
        self.assertFalse(by_status["REJECTED"]["in_zone"])
        self.assertTrue(by_status["REJECTED"]["anonymous"])

    def test_filtres_de_la_carte_admin(self):
        self.assertEqual([f["properties"]["status"] for f in self.fetch(status="PENDING")[1]["features"]], ["PENDING"])
        self.assertEqual(len(self.fetch(mode="anonymous")[1]["features"]), 1)
        self.assertEqual(len(self.fetch(zone="outside")[1]["features"]), 1)
        self.assertEqual(len(self.fetch(zone="inside")[1]["features"]), 2)
        self.assertEqual(len(self.fetch(accident_type="OTHER")[1]["features"]), 0)

    def test_filtre_invalide(self):
        response, data = self.fetch(status="XX")
        self.assertEqual(response.status_code, 400)
        self.assertIn("status", data["fields"])

    @override_settings(ADMIN_MAP_MAX_FEATURES=2)
    def test_limite(self):
        self.assertEqual(self.fetch()[1]["meta"], {"count": 2, "truncated": True})

    def test_la_carte_publique_reste_limitee_aux_verifies(self):
        body = self.client.get(reverse("maps:public_data")).json()
        self.assertEqual(body["meta"]["count"], 1)

    def test_page_de_la_carte_admin(self):
        page = self.client.get(reverse("dashboard:map"))
        for expected in ["Tous les statuts", "En attente", "Rejeté", 'name="status"', 'name="mode"', 'name="zone"', 'id="map-config"']:
            self.assertContains(page, expected)
        config = page.context["map_config"]
        self.assertTrue(config["admin"])
        self.assertEqual(config["dataUrl"], reverse("dashboard:map_data"))
        self.assertIn("{id}", config["detailUrl"])
