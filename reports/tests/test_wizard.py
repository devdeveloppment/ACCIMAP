"""Parcours de signalement en 4 étapes : structure de la page et enregistrement (la navigation entre étapes
est du JavaScript, vérifié dans un vrai navigateur par scripts/e2e_report_flow.py)."""
import re
from html import unescape
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from reports.choices import ACCIDENT_TYPE_HINTS, ACCIDENT_TYPE_ICONS, AccidentCause, AccidentType
from reports.models import AccidentReport

from .helpers import make_image, valid_report_data

EXPECTED_TYPES = [
    ("COLLISION", "Collision"), ("ROLLOVER", "Renversement"), ("RUN_OFF_ROAD", "Sortie de voie"),
    ("LOSS_OF_CONTROL", "Perte de contrôle"), ("INTERSECTION", "Accident à une intersection"),
    ("PILEUP", "Carambolage / multi-collision"), ("PEDESTRIAN", "Accident impliquant un piéton"), ("OTHER", "Autre"),
]
VEHICLE_WORDS = ("moto", "voiture", "camion", "vélo", "velo", "bus", "taxi", "véhicule seul")


class TypeListTests(TestCase):
    def test_les_huit_types_demandes_dans_l_ordre(self):
        self.assertEqual(list(AccidentType.choices), EXPECTED_TYPES)

    def test_la_nature_n_est_pas_melangee_avec_le_vehicule(self):
        for _, label in AccidentType.choices:
            for word in VEHICLE_WORDS:
                self.assertNotIn(word, label.lower(), f"« {label} » mélange nature et véhicule")

    def test_chaque_type_a_une_aide_et_une_icone_existante(self):
        icon_dir = Path(settings.BASE_DIR) / "static" / "vendor" / "lucide"
        for value in AccidentType.values:
            self.assertTrue(ACCIDENT_TYPE_HINTS[value], value)
            self.assertTrue((icon_dir / f"{ACCIDENT_TYPE_ICONS[value]}.svg").is_file(), value)
        self.assertEqual(len(set(ACCIDENT_TYPE_ICONS.values())), 8)      # icônes toutes différentes

    def test_six_causes_distinctes_des_types(self):
        self.assertEqual(
            [label for _, label in AccidentCause.choices],
            [
                "Vitesse excessive",
                "Non-respect des règles",
                "Conduite sous l'emprise des stupéfiants et de l'alcool",
                "Visibilité dégradée",
                "Défaillance mécanique",
                "Mauvaises conditions météorologiques et état de la chaussée",
            ],
        )
        self.assertEqual(len(set(AccidentCause.values)), 6)


class WizardPageTests(TestCase):
    ANONYMOUS = reverse("reports:anonymous_create")
    IDENTIFIED = reverse("reports:create")

    def test_structure_des_quatre_etapes(self):
        html = self.client.get(self.ANONYMOUS).content.decode()
        self.assertEqual(len(re.findall(r'class="report-step"', html)), 4)
        for n in (1, 2, 3, 4):
            self.assertIn(f'data-step="{n}"', html)
        for label in ("Type", "Lieu", "Détails", "Envoi"):
            self.assertRegex(html, rf'class="stepper-label">{label}<')
        for expected in ["Que s'est-il passé ?", "Où cela s'est-il passé ?", "Dites-nous en plus", "Vérifiez avant d'envoyer",
                         "L'accident s'est-il produit ici ?", "Confirmer cette position", "Envoyer le signalement"]:
            self.assertIn(expected, html)

    def test_cartes_de_types_visuelles(self):
        html = self.client.get(self.ANONYMOUS).content.decode()
        self.assertEqual(html.count('class="type-card"'), 8)
        self.assertEqual(html.count('class="type-card cause-card"'), 6)
        for value, label in EXPECTED_TYPES:
            self.assertIn(f'id="type-{value}" value="{value}"', html)
            self.assertIn(f'class="type-name">{label}<', html)
        self.assertEqual(len(re.findall(r'<span class="type-icon"><svg', html)), 14)
        for hint in ACCIDENT_TYPE_HINTS.values():
            self.assertIn(hint, html)
        self.assertIn('id="other-type-options"', html)
        self.assertRegex(html, r'id="other-type-options"[^>]*\bhidden\b')
        self.assertIn('name="accident_cause"', html)
        self.assertNotIn('id="id_accident_cause"', html)
        self.assertNotIn("Causes de l'accident", html)
        self.assertEqual(
            [(value, unescape(label)) for value, label in re.findall(
                r'id="cause-([A-Z_]+)" value="[^"]+"[^>]*>.*?<span class="type-name">([^<]+)<',
                html,
                re.S,
            )],
            [
                ("EXCESSIVE_SPEED", "Vitesse excessive"),
                ("RULE_VIOLATION", "Non-respect des règles"),
                ("IMPAIRED_DRIVING", "Conduite sous l'emprise des stupéfiants et de l'alcool"),
                ("REDUCED_VISIBILITY", "Visibilité dégradée"),
                ("MECHANICAL_FAILURE", "Défaillance mécanique"),
                ("WEATHER_AND_ROAD_CONDITIONS", "Mauvaises conditions météorologiques et état de la chaussée"),
            ],
        )

    def test_aucun_choix_par_defaut_pour_le_type(self):
        html = self.client.get(self.ANONYMOUS).content.decode()
        type_grid = re.search(r'<div class="type-grid".*?</div>\s*</fieldset>', html, re.S)
        self.assertIsNotNone(type_grid)
        self.assertNotIn("checked", type_grid.group(0))

    def test_etape_lieu(self):
        page = self.client.get(self.ANONYMOUS)
        for expected in ['id="location-map"', 'id="id_latitude"', 'id="id_longitude"', "Latitude", "Longitude",
                         'id="place-label"', 'id="confirm-position-btn"', "Utiliser ma position actuelle",
                         'id="outside-zone-box"', "Déplacez le repère"]:
            self.assertContains(page, expected)
        self.assertRegex(page.content.decode(), r'id="outside-zone-box"[^>]*\bhidden\b')

    def test_etape_details_courte(self):
        page = self.client.get(self.ANONYMOUS)
        for expected in ["Gravité estimée", "Faible", "Très grave", "Véhicules", "facultatif",
                         "Laissez vide si vous ne savez pas", "Blessés", "Décès",
                         'name="description"', "Ajouter des photos", 'accept="image/jpeg,image/png,image/webp"',
                         'name="accident_date"', 'name="accident_time"']:
            self.assertContains(page, expected)
        self.assertEqual(len(re.findall(r'class="counter[ "]', page.content.decode())), 3)   # blessés, décès, véhicules

    def test_limites_des_compteurs(self):
        html = self.client.get(self.ANONYMOUS).content.decode()
        for name, maximum in (("vehicle_count", 100), ("injured_count", 500), ("death_count", 500)):
            self.assertRegex(html, rf'<input[^>]*name="{name}"[^>]*max="{maximum}"')

    def test_etape_recapitulatif(self):
        page = self.client.get(self.ANONYMOUS)
        for expected in ['id="recap"', 'id="recap-type"', 'id="recap-place"', 'id="recap-coords"', 'id="recap-datetime"',
                         'id="recap-severity"', 'id="recap-counts"', 'id="recap-photos"', 'id="recap-declarant"']:
            self.assertContains(page, expected)
        self.assertNotContains(page, 'id="recap-cause-row"')
        self.assertContains(page, 'type="submit" class="btn btn-accent btn-lg flex-grow-1" id="submit-report"')

    def test_mode_anonyme(self):
        page = self.client.get(self.ANONYMOUS)
        self.assertContains(page, "Aucun compte requis.")
        self.assertContains(page, 'data-anonymous="1"')
        self.assertContains(page, "Aucune identité ni numéro de téléphone")
        self.assertNotContains(page, "+228")

    def test_mode_identifie_numero_masque(self):
        user = User.objects.create_user("90123456")
        self.client.force_login(user)
        page = self.client.get(self.IDENTIFIED)
        self.assertContains(page, 'data-anonymous="1"')
        self.assertContains(page, "Aucun compte requis")
        self.assertNotContains(page, "+22890123456")
        self.assertNotContains(page, "90123456")

    def test_les_deux_parcours_utilisent_le_meme_gabarit(self):
        self.client.force_login(User.objects.create_user("90123456"))
        a = self.client.get(self.ANONYMOUS).content.decode()
        b = self.client.get(self.IDENTIFIED).content.decode()
        for html in (a, b):
            self.assertEqual(html.count('class="report-step"'), 4)
            self.assertEqual(html.count('class="type-card"'), 8)

    def test_script_et_configuration(self):
        page = self.client.get(self.ANONYMOUS)
        self.assertContains(page, "js/report_wizard.js")
        config = page.context["map_config"]
        self.assertEqual(config["placeUrl"], "/report/place/")
        self.assertEqual(config["checkUrl"], "/report/check-position/")
        self.assertEqual(len(page.context["type_cards"]), 8)

    def test_repli_sans_javascript_toutes_les_etapes_sont_dans_la_page(self):
        html = self.client.get(self.ANONYMOUS).content.decode()
        self.assertIn("document.documentElement.classList.add('js')", html)   # le masquage n'est actif qu'avec JS
        self.assertNotIn("is-active", html)                                   # aucune étape n'est masquée côté serveur


class WizardSubmissionTests(TestCase):
    URLS = {"formulaire public": reverse("reports:create"), "ancienne URL": reverse("reports:anonymous_create")}

    def test_chaque_type_s_enregistre_dans_les_deux_parcours(self):
        for mode, url in self.URLS.items():
            for value, label in AccidentType.choices:
                with self.subTest(mode=mode, type=value):
                    response = self.client.post(url, valid_report_data(accident_type=value, latitude="6.140000", longitude="1.220000"))
                    self.assertEqual(response.status_code, 302)
                    report = AccidentReport.objects.latest("created_at")
                    self.assertEqual(report.accident_type, value)
                    self.assertEqual(report.get_accident_type_display(), label)
                    self.assertTrue(report.is_anonymous)
                    self.assertIsNone(report.user)
        self.assertEqual(AccidentReport.objects.count(), 16)

    def test_chaque_precision_est_enregistree_avec_le_type_autre(self):
        for cause, label in AccidentCause.choices:
            with self.subTest(cause=cause):
                response = self.client.post(
                    reverse("reports:create"),
                    valid_report_data(accident_type="OTHER", accident_cause=cause),
                )
                self.assertEqual(response.status_code, 302)
                report = AccidentReport.objects.latest("created_at")
                self.assertEqual(report.accident_type, "OTHER")
                self.assertEqual(report.accident_cause, cause)
                self.assertEqual(report.get_accident_cause_display(), label)

    def test_cause_facultative_et_type_existant_conserve(self):
        response = self.client.post(
            self.URLS["formulaire public"],
            valid_report_data(accident_type="ROLLOVER"),
        )

        self.assertEqual(response.status_code, 302)
        report = AccidentReport.objects.get()
        self.assertEqual(report.accident_type, "ROLLOVER")
        self.assertIsNone(report.accident_cause)
        self.assertTrue(report.is_anonymous)
        self.assertIsNone(report.user)

    def test_cause_invalide_est_refusee(self):
        response = self.client.post(
            self.URLS["formulaire public"],
            valid_report_data(accident_type="OTHER", accident_cause="NOT_A_CAUSE"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("accident_cause", response.context["form"].errors)
        self.assertEqual(AccidentReport.objects.count(), 0)

    def test_precision_refusee_si_le_type_n_est_pas_autre(self):
        response = self.client.post(
            self.URLS["formulaire public"],
            valid_report_data(accident_type="COLLISION", accident_cause="EXCESSIVE_SPEED"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("accident_cause", response.context["form"].errors)
        self.assertEqual(AccidentReport.objects.count(), 0)

    def test_coordonnees_choisies_conservees_dans_le_pointfield(self):
        self.client.post(self.URLS["ancienne URL"], valid_report_data(latitude="6.1319123", longitude="1.2228987"))
        report = AccidentReport.objects.get()
        self.assertEqual((report.location.y, report.location.x), (6.1319123, 1.2228987))
        self.assertEqual(report.location.srid, 4326)

    def test_anciens_types_refuses(self):
        for old in ("MOTORCYCLE", "COLLISION_VEHICLES", "COLLISION_PEDESTRIAN", "SINGLE_VEHICLE", "n'importe quoi"):
            with self.subTest(old=old):
                response = self.client.post(self.URLS["ancienne URL"], valid_report_data(accident_type=old))
                self.assertEqual(response.status_code, 200)
                self.assertIn("accident_type", response.context["form"].errors)
        self.assertEqual(AccidentReport.objects.count(), 0)

    def test_type_manquant_ouvre_l_erreur_et_conserve_le_reste(self):
        data = valid_report_data(accident_type="", description="Texte à conserver", latitude="6.150000", longitude="1.230000")
        page = self.client.post(self.URLS["ancienne URL"], data)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Ce champ est obligatoire")
        self.assertContains(page, "Texte à conserver")
        self.assertContains(page, 'value="6.150000"')
        self.assertContains(page, 'value="1.230000"')

    def test_type_choisi_conserve_apres_une_erreur_ailleurs(self):
        page = self.client.post(self.URLS["ancienne URL"], valid_report_data(accident_type="PILEUP", injured_count="-4"))
        self.assertEqual(page.status_code, 200)
        self.assertRegex(page.content.decode(), r'id="type-PILEUP" value="PILEUP"[^>]*checked')

    def test_photo_perdue_apres_erreur_est_signalee_a_l_utilisateur(self):
        data = {**valid_report_data(injured_count="-4"), "photos": make_image()}
        self.assertContains(self.client.post(self.URLS["ancienne URL"], data), "ne conserve pas les photos")

    def test_pas_de_message_photo_sans_photo(self):
        page = self.client.post(self.URLS["ancienne URL"], valid_report_data(injured_count="-4"))
        self.assertNotContains(page, "ne conserve pas les photos")

    def test_photo_valide_toujours_nettoyee_dans_le_nouveau_parcours(self):
        import tempfile
        from django.test import override_settings
        from reports.tests.helpers import make_jpeg_with_exif
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile("IMG_Jean.jpg", make_jpeg_with_exif(), content_type="image/jpeg")
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            self.client.post(self.URLS["ancienne URL"], {**valid_report_data(), "photos": upload})
            data = Path(AccidentReport.objects.get().photo.path).read_bytes()
            for marker in (b"Exif", b"Apple", b"iPhone", b"GPS"):
                self.assertNotIn(marker, data)

    def test_confirmation_apres_enregistrement(self):
        response = self.client.post(self.URLS["ancienne URL"], valid_report_data(), follow=True)
        self.assertContains(response, "Votre signalement a été enregistré anonymement.")
        self.assertContains(response, "en attente de vérification")
