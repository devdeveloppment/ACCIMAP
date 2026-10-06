import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from reports.images import sanitize_photo

from .helpers import make_image, make_jpeg_with_exif

GPS_IFD, MAKE = 0x8825, 0x010F


def reopen(uploaded):
    data = uploaded.read()
    return data, Image.open(io.BytesIO(data))


class ExifRemovalTests(SimpleTestCase):
    def test_le_jpeg_de_depart_contient_bien_des_donnees_personnelles(self):
        original = Image.open(io.BytesIO(make_jpeg_with_exif()))
        self.assertEqual(original.getexif().get(MAKE), "Apple")
        self.assertTrue(original.getexif().get_ifd(GPS_IFD))

    def test_jpeg_gps_marque_auteur_supprimes(self):
        out = sanitize_photo(SimpleUploadedFile("IMG_0042_Jean_Dupont.jpg", make_jpeg_with_exif()))
        data, image = reopen(out)
        self.assertEqual(dict(image.getexif()), {})
        self.assertFalse(image.getexif().get_ifd(GPS_IFD))
        for marker in (b"Exif", b"Apple", b"iPhone", b"Dupont", b"GPS"):
            self.assertNotIn(marker, data)
        for key in ("exif", "icc_profile", "xmp", "comment"):
            self.assertNotIn(key, image.info)

    def test_nom_d_origine_abandonne(self):
        out = sanitize_photo(SimpleUploadedFile("IMG_0042_Jean_Dupont.jpg", make_jpeg_with_exif()))
        self.assertEqual(out.name, "photo.jpg")
        self.assertEqual(out.content_type, "image/jpeg")

    def test_orientation_appliquee_avant_suppression(self):
        # Orientation 6 : une photo 400x200 doit ressortir 200x400, à l'endroit.
        out = sanitize_photo(SimpleUploadedFile("a.jpg", make_jpeg_with_exif((400, 200))))
        self.assertEqual(reopen(out)[1].size, (200, 400))

    def test_png_avec_texte_et_profil_nettoye(self):
        from PIL.PngImagePlugin import PngInfo

        meta = PngInfo()
        meta.add_text("Author", "Jean Dupont")
        meta.add_text("GPS", "6.13,1.22")
        buffer = io.BytesIO()
        Image.new("RGB", (30, 30), "red").save(buffer, "PNG", pnginfo=meta)
        self.assertIn(b"Dupont", buffer.getvalue())

        data, image = reopen(sanitize_photo(SimpleUploadedFile("a.png", buffer.getvalue())))
        self.assertNotIn(b"Dupont", data)
        self.assertNotIn(b"GPS", data)
        self.assertEqual(image.format, "PNG")

    def test_webp_avec_exif_nettoye(self):
        exif = Image.Exif()
        exif[MAKE] = "Samsung"
        buffer = io.BytesIO()
        Image.new("RGB", (30, 30), "blue").save(buffer, "WEBP", exif=exif)
        self.assertIn(b"Samsung", buffer.getvalue())

        data, image = reopen(sanitize_photo(SimpleUploadedFile("a.webp", buffer.getvalue())))
        self.assertNotIn(b"Samsung", data)
        self.assertEqual(image.format, "WEBP")


class ReencodingTests(SimpleTestCase):
    def test_grande_image_reduite(self):
        big = make_image("big.png", "PNG", size=(3000, 1500))
        _, image = reopen(sanitize_photo(big, max_dimension=1000))
        self.assertEqual(image.size, (1000, 500))

    @override_settings(PHOTO_MAX_DIMENSION=800)
    def test_dimension_maximale_configurable(self):
        _, image = reopen(sanitize_photo(make_image("big.jpg", "JPEG", size=(2000, 1000))))
        self.assertEqual(max(image.size), 800)

    def test_petite_image_non_agrandie(self):
        self.assertEqual(reopen(sanitize_photo(make_image(size=(50, 40))))[1].size, (50, 40))

    def test_transparence_png_conservee(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (20, 20), (255, 0, 0, 0)).save(buffer, "PNG")
        _, image = reopen(sanitize_photo(SimpleUploadedFile("t.png", buffer.getvalue())))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((5, 5))[3], 0)

    def test_png_palette_avec_transparence(self):
        buffer = io.BytesIO()
        palette = Image.new("P", (20, 20), 0)
        palette.info["transparency"] = 0
        palette.save(buffer, "PNG", transparency=0)
        _, image = reopen(sanitize_photo(SimpleUploadedFile("p.png", buffer.getvalue())))
        self.assertEqual(image.convert("RGBA").getpixel((5, 5))[3], 0)

    def test_jpeg_cmyk_converti(self):
        buffer = io.BytesIO()
        Image.new("CMYK", (20, 20), (0, 0, 0, 0)).save(buffer, "JPEG")
        _, image = reopen(sanitize_photo(SimpleUploadedFile("c.jpg", buffer.getvalue())))
        self.assertEqual(image.mode, "RGB")

    def test_contenu_visuel_conserve(self):
        _, image = reopen(sanitize_photo(make_image(size=(20, 20))))
        self.assertEqual(image.convert("RGB").getpixel((10, 10)), (200, 30, 30))

    def test_fichier_non_image_refuse(self):
        with self.assertRaises(ValidationError):
            sanitize_photo(SimpleUploadedFile("x.jpg", b"<?php echo 1; ?>"))

    def test_format_non_autorise_refuse(self):
        with self.assertRaises(ValidationError):
            sanitize_photo(make_image("x.png", "GIF"))
