from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.backends import PhoneModelBackend
from accounts.models import User
from accounts.phone import mask_phone, normalize_phone


class NormalizePhoneTests(TestCase):
    def test_formats_valides_donnent_le_meme_numero(self):
        for raw in ["90 12 34 56", "+228 90 12 34 56", "00228 90123456", "22890123456", "90-12-34-56"]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), "+22890123456")

    def test_numero_etranger_accepte(self):
        self.assertEqual(normalize_phone("+33 6 12 34 56 78"), "+33612345678")

    def test_numeros_invalides(self):
        for raw in ["", None, "abc", "123", "+228 901234", "+228 901234567", "0"]:
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                normalize_phone(raw)

    def test_masquage(self):
        self.assertEqual(mask_phone("+22890123456"), "+228****56")
        self.assertEqual(mask_phone(""), "****")


class UserTests(TestCase):
    def test_create_user_normalise_et_sans_mot_de_passe(self):
        user = User.objects.create_user("90 12 34 56")
        self.assertEqual(user.phone_number, "+22890123456")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_superuser_exige_un_mot_de_passe(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser("90123456")
        admin = User.objects.create_superuser("90123457", "motdepasse-solide-42")
        self.assertTrue(admin.is_staff and admin.is_superuser)

    def test_numero_unique(self):
        User.objects.create_user("90123456")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user("+228 90 12 34 56")

    def test_backend_accepte_tous_les_formats_de_numero(self):
        User.objects.create_superuser("90123456", "motdepasse-solide-42")
        backend = PhoneModelBackend()
        self.assertIsNotNone(backend.authenticate(None, username="90 12 34 56", password="motdepasse-solide-42"))
        self.assertIsNone(backend.authenticate(None, username="90123456", password="mauvais"))
        self.assertIsNone(backend.authenticate(None, username="n'importe quoi", password="x"))
