import os
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from reports.models import report_photo_path
from reports.validators import (
    validate_photo_content,
    validate_photo_extension,
    validate_photo_size,
)

from .helpers import make_image, make_report


class PhotoValidatorTests(SimpleTestCase):
    def test_image_valide_acceptee(self):
        for name, fmt in [("a.png", "PNG"), ("b.jpg", "JPEG"), ("c.jpeg", "JPEG"), ("d.webp", "WEBP")]:
            with self.subTest(name=name):
                photo = make_image(name, fmt)
                validate_photo_extension(photo)
                validate_photo_size(photo)
                validate_photo_content(photo)

    def test_extension_interdite(self):
        for name in ["virus.exe", "page.php", "anim.gif", "noext"]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_photo_extension(SimpleUploadedFile(name, b"x"))

    def test_faux_jpg_refuse(self):
        fake = SimpleUploadedFile("photo.jpg", b"<?php echo 'pirate'; ?>", content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_photo_content(fake)

    def test_image_corrompue_refusee(self):
        valid = make_image().read()
        broken = SimpleUploadedFile("cassee.png", valid[:30])
        with self.assertRaises(ValidationError):
            validate_photo_content(broken)

    def test_format_non_autorise_meme_si_c_est_une_image(self):
        gif = make_image("photo.png", "GIF")  # extension trompeuse, contenu GIF
        with self.assertRaises(ValidationError):
            validate_photo_content(gif)

    @override_settings(MAX_UPLOAD_SIZE_MB=1)
    def test_taille_maximale(self):
        big = SimpleUploadedFile("grosse.png", b"0" * (1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            validate_photo_size(big)
        validate_photo_size(SimpleUploadedFile("petite.png", b"0" * 1000))

    def test_pointeur_de_fichier_remis_a_zero(self):
        photo = make_image()
        validate_photo_content(photo)
        self.assertEqual(photo.tell(), 0)


class PhotoStorageTests(TestCase):
    def test_nom_de_fichier_securise(self):
        path = report_photo_path(None, "../../Mon accident é.PNG")
        self.assertRegex(path, r"^reports/photos/\d{4}/\d{2}/[0-9a-f]{32}\.png$")
        self.assertNotIn("accident", path)
        self.assertTrue(report_photo_path(None, "shell.php").endswith(".bin"))

    def test_photo_enregistree_via_l_api_de_stockage(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            report = make_report(photo=make_image("original.png"))
            report.full_clean()
            self.assertTrue(report.photo.name.startswith("reports/photos/"))
            self.assertNotIn("original", report.photo.name)
            self.assertTrue(os.path.exists(os.path.join(tmp, report.photo.name)))
