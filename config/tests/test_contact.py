"""Tests du formulaire et de l'envoi de contact."""
import smtplib
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


CONTACT_DATA = {
    "name": "Afi Mensah",
    "email": "afi@example.com",
    "subject": "Question sur ACCIMAP",
    "message": "Bonjour, je souhaite en savoir plus sur le projet.",
}

TEST_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "EMAIL_HOST": "smtp.gmail.com",
    "EMAIL_HOST_USER": "sender@example.com",
    "EMAIL_HOST_PASSWORD": "test-only-password",
    "DEFAULT_FROM_EMAIL": "sender@example.com",
    "CONTACT_RECEIVER_EMAIL": "atou1926@gmail.com",
    "CACHES": {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
}


@override_settings(**TEST_SETTINGS)
class ContactPageTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def test_page_affiche_coordonnees_liens_et_structure_mobile(self):
        response = self.client.get(reverse("contact"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="tel:91540386"')
        self.assertContains(response, 'href="mailto:atou1926@gmail.com"')
        self.assertContains(response, 'name="viewport"')
        self.assertContains(response, "col-lg-7")
        self.assertContains(response, "col-lg-5")

    def test_message_valide_est_envoye_au_destinataire_et_confirme(self):
        response = self.client.post(reverse("contact"), CONTACT_DATA)

        self.assertRedirects(response, reverse("contact"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["atou1926@gmail.com"])
        self.assertEqual(mail.outbox[0].reply_to, ["afi@example.com"])
        self.assertIn("Question sur ACCIMAP", mail.outbox[0].subject)
        confirmation = self.client.get(response["Location"])
        self.assertContains(confirmation, "Votre message a bien été envoyé")

    def test_champs_invalides_ne_declenchent_pas_d_envoi(self):
        response = self.client.post(
            reverse("contact"),
            {**CONTACT_DATA, "email": "adresse-invalide", "subject": ""},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Adresse e-mail", status_code=400)
        self.assertEqual(len(mail.outbox), 0)

    def test_piege_antibot_refuse_la_soumission(self):
        response = self.client.post(reverse("contact"), {**CONTACT_DATA, "website": "spam"})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Vérifiez les champs et réessayez", status_code=400)
        self.assertEqual(len(mail.outbox), 0)

    def test_limitation_de_debit_empeche_les_envois_excessifs(self):
        with override_settings(CONTACT_RATE_LIMIT=1):
            first_response = self.client.post(reverse("contact"), CONTACT_DATA)
            second_response = self.client.post(reverse("contact"), CONTACT_DATA)

        self.assertRedirects(first_response, reverse("contact"))
        self.assertEqual(second_response.status_code, 429)
        self.assertContains(second_response, "Trop de messages", status_code=429)
        self.assertEqual(len(mail.outbox), 1)

    @patch("config.views.EmailMessage.send", side_effect=smtplib.SMTPException("connection failed"))
    def test_echec_smtp_ne_declenche_pas_de_fausse_confirmation(self, email_send_mock):
        response = self.client.post(reverse("contact"), CONTACT_DATA)

        self.assertEqual(response.status_code, 503)
        self.assertIn("n&#x27;a pas pu", response.content.decode())
        self.assertNotIn("Votre message a bien été envoyé", response.content.decode())
        email_send_mock.assert_called_once_with(fail_silently=False)

    @patch("config.views.EmailMessage.send", return_value=0)
    def test_absence_de_confirmation_smtp_ne_declenche_pas_de_succes(self, email_send_mock):
        response = self.client.post(reverse("contact"), CONTACT_DATA)

        self.assertEqual(response.status_code, 503)
        self.assertIn("n&#x27;a pas pu", response.content.decode())
        email_send_mock.assert_called_once_with(fail_silently=False)

    def test_configuration_smtp_absente_est_signalee_sans_envoi(self):
        with override_settings(EMAIL_HOST_PASSWORD=""):
            response = self.client.post(reverse("contact"), CONTACT_DATA)

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "temporairement indisponible", status_code=503)
        self.assertEqual(len(mail.outbox), 0)
