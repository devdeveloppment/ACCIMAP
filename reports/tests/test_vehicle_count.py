"""Nombre approximatif de véhicules impliqués : facultatif (vide = non renseigné, différent de 0)."""
import re

from django.test import TestCase
from django.urls import reverse

from reports.forms import AccidentReportForm
from reports.models import AccidentReport

from .helpers import make_report, valid_report_data

ANONYMOUS = reverse("reports:anonymous_create")


def without(data, key):
    return {k: v for k, v in data.items() if k != key}


class VehicleFieldFormTests(TestCase):
    def build(self, **overrides):
        return AccidentReportForm(data=valid_report_data(**overrides))

    def test_champ_absent_est_valide_et_non_renseigne(self):
        form = AccidentReportForm(data=without(valid_report_data(), "vehicle_count"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["vehicle_count"])

    def test_champ_vide_est_valide(self):
        form = self.build(vehicle_count="")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["vehicle_count"])

    def test_zero_est_une_vraie_valeur_differente_de_vide(self):
        form = self.build(vehicle_count="0")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["vehicle_count"], 0)

    def test_valeurs_limites(self):
        for value in ("1", "2", "100"):
            with self.subTest(value=value):
                self.assertTrue(self.build(vehicle_count=value).is_valid())

    def test_valeurs_refusees(self):
        for value in ("101", "-1", "abc", "2.5", "1000"):
            with self.subTest(value=value):
                form = self.build(vehicle_count=value)
                self.assertFalse(form.is_valid())
                self.assertIn("vehicle_count", form.errors)

    def test_libelle_demande(self):
        self.assertEqual(AccidentReportForm().fields["vehicle_count"].label, "Nombre approximatif de véhicules impliqués")
        self.assertFalse(AccidentReportForm().fields["vehicle_count"].required)

    def test_blesses_et_deces_restent_obligatoires(self):
        for name in ("injured_count", "death_count"):
            form = AccidentReportForm(data=without(valid_report_data(), name))
            self.assertFalse(form.is_valid(), name)
            self.assertIn(name, form.errors)


class VehicleFieldModelTests(TestCase):
    def test_pas_de_valeur_par_defaut(self):
        self.assertFalse(AccidentReport._meta.get_field("vehicle_count").has_default())
        self.assertIsNone(make_report().vehicle_count)

    def test_null_et_zero_sont_distincts_en_base(self):
        none = make_report(vehicle_count=None)
        zero = make_report(vehicle_count=0)
        none.refresh_from_db(); zero.refresh_from_db()
        self.assertIsNone(none.vehicle_count)
        self.assertEqual(zero.vehicle_count, 0)
        self.assertEqual(AccidentReport.objects.filter(vehicle_count__isnull=True).count(), 1)

    def test_validation_du_modele(self):
        from django.core.exceptions import ValidationError
        report = make_report(vehicle_count=None)
        report.full_clean()                                  # None accepté
        report.vehicle_count = 101
        with self.assertRaises(ValidationError):
            report.full_clean()


class VehicleFieldJourneyTests(TestCase):
    def test_page_sans_valeur_preselectionnee(self):
        html = self.client.get(ANONYMOUS).content.decode()
        field = re.search(r'<input[^>]*name="vehicle_count"[^>]*>', html).group(0)
        self.assertNotRegex(field, r'value="\d')                  # ni 1 ni aucune valeur par défaut
        self.assertIn('placeholder="—"', field)
        self.assertIn('min="0"', field)
        self.assertIn('max="100"', field)
        self.assertIn('inputmode="numeric"', field)                  # clavier numérique sur mobile
        self.assertNotIn("required", field)

    def test_controle_numerique_adapte_au_mobile(self):
        html = self.client.get(ANONYMOUS).content.decode()
        counter = re.search(r'<div class="counter counter-wide" id="vehicle-counter">.*?</div>\s*<div class="form-text">', html, re.S).group(0)
        self.assertEqual(counter.count('class="counter-btn"'), 2)    # boutons « − » et « + »
        self.assertIn('aria-label="Diminuer', counter)
        self.assertIn('aria-label="Augmenter', counter)

    def test_enregistrement_sans_vehicules_dans_les_deux_parcours(self):
        from accounts.models import User
        self.client.force_login(User.objects.create_user("90123456"))
        for url in (ANONYMOUS, reverse("reports:create")):
            with self.subTest(url=url):
                response = self.client.post(url, without(valid_report_data(), "vehicle_count"))
                self.assertEqual(response.status_code, 302)
                self.assertIsNone(AccidentReport.objects.latest("created_at").vehicle_count)

    def test_enregistrement_avec_vehicules(self):
        self.client.post(ANONYMOUS, valid_report_data(vehicle_count="3"))
        self.assertEqual(AccidentReport.objects.get().vehicle_count, 3)

    def test_erreur_affichee_sur_la_valeur_invalide(self):
        page = self.client.post(ANONYMOUS, valid_report_data(vehicle_count="500"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(AccidentReport.objects.count(), 0)
        self.assertIn("vehicle_count", page.context["form"].errors)
        self.assertContains(page, "invalid-feedback")

    def test_la_valeur_saisie_est_conservee_apres_une_erreur_ailleurs(self):
        page = self.client.post(ANONYMOUS, valid_report_data(vehicle_count="4", injured_count="-2"))
        self.assertRegex(page.content.decode(), r'<input[^>]*name="vehicle_count"[^>]*value="4"')

    def test_la_valeur_vide_reste_vide_apres_une_erreur_ailleurs(self):
        page = self.client.post(ANONYMOUS, valid_report_data(vehicle_count="", injured_count="-2"))
        self.assertNotRegex(page.content.decode(), r'<input[^>]*name="vehicle_count"[^>]*value="\d')

    def test_pas_de_nouveau_type_d_accident(self):
        from reports.choices import AccidentType
        self.assertEqual(len(AccidentType.values), 8)          # le champ n'a pas créé de type « véhicule »
