from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from reports.forms import AccidentReportForm

from .helpers import make_image, make_jpeg_with_exif, valid_report_data


def build(data=None, files=None):
    return AccidentReportForm(data=data if data is not None else valid_report_data(), files=files)


class ValidReportTests(TestCase):
    def test_donnees_valides(self):
        form = build()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.outside_zone)

    def test_coordonnees_conservees_telles_quelles(self):
        form = build(valid_report_data(latitude="6.1319123456", longitude="1.2228987654"))
        self.assertTrue(form.is_valid(), form.errors)
        point = form.instance.location
        self.assertEqual((point.y, point.x), (6.1319123456, 1.2228987654))
        self.assertEqual(point.srid, 4326)

    def test_latitude_longitude_derivees_apres_enregistrement(self):
        form = build()
        report = form.save(commit=False)
        report.save()
        report.refresh_from_db()
        self.assertAlmostEqual(report.latitude, 6.1319)
        self.assertAlmostEqual(report.longitude, 1.2228)

    def test_champs_facultatifs(self):
        form = build(valid_report_data(description=""))
        self.assertTrue(form.is_valid(), form.errors)


class CoordinateValidationTests(TestCase):
    def test_position_manquante(self):
        data = valid_report_data()
        del data["latitude"], data["longitude"]
        form = build(data)
        self.assertFalse(form.is_valid())
        self.assertIn("Veuillez choisir la position", form.errors["latitude"][0])
        self.assertIn("Veuillez choisir la position", form.errors["longitude"][0])

    def test_coordonnees_invalides_refusees(self):
        cases = [
            ("latitude", "91"), ("latitude", "-91"), ("latitude", "abc"), ("latitude", "nan"),
            ("latitude", "inf"), ("longitude", "181"), ("longitude", "-181"),
            ("longitude", "abc"), ("longitude", "nan"), ("longitude", "-inf"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                form = build(valid_report_data(**{field: value}))
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_limites_exactes_acceptees(self):
        form = build(valid_report_data(latitude="90", longitude="180", confirm_outside_zone="on"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_position_zero_zero_refusee(self):
        form = build(valid_report_data(latitude="0", longitude="0", confirm_outside_zone="on"))
        self.assertFalse(form.is_valid())
        self.assertIn("(0, 0)", form.errors["latitude"][0])


class OutsideZoneTests(TestCase):
    """Hors zone : avertissement + confirmation, jamais un refus définitif."""

    PARIS = dict(latitude="48.8566", longitude="2.3522")

    def test_hors_zone_sans_confirmation_demande_confirmation(self):
        form = build(valid_report_data(**self.PARIS))
        self.assertFalse(form.is_valid())
        self.assertTrue(form.outside_zone)
        self.assertIn("en dehors de la zone couverte par ACCIMAP", form.errors["confirm_outside_zone"][0])

    def test_hors_zone_avec_confirmation_est_accepte(self):
        form = build(valid_report_data(confirm_outside_zone="on", **self.PARIS))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.outside_zone)

    def test_coordonnees_hors_zone_jamais_modifiees(self):
        form = build(valid_report_data(confirm_outside_zone="on", **self.PARIS))
        self.assertTrue(form.is_valid(), form.errors)
        report = form.save(commit=False)
        report.save()
        report.refresh_from_db()
        self.assertEqual((report.location.y, report.location.x), (48.8566, 2.3522))
        self.assertAlmostEqual(report.latitude, 48.8566)
        self.assertAlmostEqual(report.longitude, 2.3522)

    def test_dans_la_zone_aucune_confirmation_requise(self):
        form = build(valid_report_data(latitude="6.1725", longitude="1.2314"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.outside_zone)


class FieldValidationTests(TestCase):
    def assertInvalid(self, field, **overrides):
        form = build(valid_report_data(**overrides))
        self.assertFalse(form.is_valid(), f"{overrides} devrait être refusé")
        self.assertIn(field, form.errors)

    def test_type_obligatoire_et_valide(self):
        self.assertInvalid("accident_type", accident_type="")
        self.assertInvalid("accident_type", accident_type="INCONNU")

    def test_gravite_valide(self):
        self.assertInvalid("severity", severity="TERRIBLE")

    def test_date_future_refusee(self):
        self.assertInvalid("accident_date", accident_date=(timezone.localdate() + timedelta(days=3)).isoformat())

    def test_date_et_heure_obligatoires(self):
        self.assertInvalid("accident_date", accident_date="")
        self.assertInvalid("accident_time", accident_time="")
        self.assertInvalid("accident_date", accident_date="pas-une-date")

    def test_nombres_negatifs_ou_excessifs(self):
        self.assertInvalid("injured_count", injured_count="-1")
        self.assertInvalid("death_count", death_count="-3")
        self.assertInvalid("vehicle_count", vehicle_count="101")
        self.assertInvalid("injured_count", injured_count="501")
        self.assertInvalid("injured_count", injured_count="beaucoup")

    def test_description_trop_longue(self):
        self.assertInvalid("description", description="x" * 2001)


class PhotoFormTests(TestCase):
    def test_photo_valide_est_nettoyee_avant_stockage(self):
        upload = SimpleUploadedFile("IMG_Jean_Dupont.jpg", make_jpeg_with_exif(), content_type="image/jpeg")
        form = build(files={"photos": upload})
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data["photos"][0]
        self.assertEqual(cleaned.name, "photo.jpg")
        self.assertNotIn(b"Exif", cleaned.read())  # nettoyée AVANT tout enregistrement

    def test_sans_photo(self):
        form = build()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["photos"])

    def test_faux_jpg_refuse(self):
        upload = SimpleUploadedFile("photo.jpg", b"<?php system($_GET['c']); ?>", content_type="image/jpeg")
        self.assertFalse(build(files={"photos": upload}).is_valid())

    def test_extension_interdite_refusee(self):
        form = build(files={"photos": make_image("anim.gif", "GIF")})
        self.assertFalse(form.is_valid())
        self.assertIn("photos", form.errors)

    @override_settings(MAX_UPLOAD_SIZE_MB=1)
    def test_photo_trop_volumineuse_refusee(self):
        big = SimpleUploadedFile("grosse.png", b"0" * (1024 * 1024 + 1), content_type="image/png")
        form = build(files={"photos": big})
        self.assertFalse(form.is_valid())
        self.assertIn("photos", form.errors)

    def test_plusieurs_photos_acceptees(self):
        form = build(files={"photos": [make_image("a.png"), make_image("b.png")]})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data["photos"]), 2)

    @override_settings(MAX_PHOTOS_PER_REPORT=2)
    def test_trop_de_photos_refusees(self):
        form = build(files={"photos": [make_image("a.png"), make_image("b.png"), make_image("c.png")]})
        self.assertFalse(form.is_valid())
        self.assertIn("photos", form.errors)
