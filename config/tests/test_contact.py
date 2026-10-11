"""Tests de la page de contact WhatsApp."""
from django.test import TestCase
from django.urls import reverse


class ContactPageTests(TestCase):
    def test_page_propose_le_contact_whatsapp_et_est_adaptee_au_mobile(self):
        response = self.client.get(reverse("contact"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://wa.me/22891540386")
        self.assertContains(response, "Bonjour%2C%20je%20vous%20contacte%20via%20ACCIMAP.")
        self.assertContains(response, "+228 91 54 03 86")
        self.assertContains(response, 'name="viewport"')
        self.assertContains(response, 'target="_blank" rel="noopener noreferrer"')
        self.assertNotContains(response, "<form")
        self.assertNotContains(response, "mailto:")
        self.assertNotContains(response, "smtp.gmail.com")

    def test_la_page_n_accepte_pas_de_soumission_de_formulaire(self):
        response = self.client.post(reverse("contact"))

        self.assertEqual(response.status_code, 405)
