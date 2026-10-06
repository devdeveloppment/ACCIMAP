"""
SMS SIMULÉ (ConsoleSMSBackend) : seul le TRANSPORT est simulé ; génération, empreinte, expiration, tentatives,
renvoi, plafond horaire, validation et connexion sont ceux de la production.

Tous ces tests tournent RÉSEAU COUPÉ (toute connexion socket Python lève une erreur) et sans clé Brevo.
"""
import io
import re
import socket
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.checks import run_checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import OTPCode, User
from accounts.otp import OTPDeliveryError, OTPInvalid, OTPTooManyAttempts, request_otp, verify_otp
from accounts.sms import BrevoSMSBackend, ConsoleSMSBackend, SMSError, get_sms_backend

PHONE = "90123456"
FULL_PHONE = "+22890123456"
MASKED = "+228****56"
SIMULATION = dict(OTP_DEV_MODE=True, SMS_BACKEND="accounts.sms.ConsoleSMSBackend", SMS_API_KEY="",
                  OTP_RESEND_DELAY_SECONDS=0, DEBUG=True)


def _no_network(*args, **kwargs):
    raise OSError("Accès réseau interdit pendant les tests de simulation SMS.")


class OfflineMixin:
    """Coupe le réseau (sockets Python) et interdit tout appel au fournisseur réel pendant chaque test."""

    def setUp(self):
        super().setUp()
        for target in (patch.object(socket.socket, "connect", _no_network),
                       patch("socket.create_connection", _no_network),
                       patch("accounts.sms.urlopen", side_effect=AssertionError("appel Brevo interdit")),
                       patch.object(BrevoSMSBackend, "send", side_effect=AssertionError("SMS réel interdit"))):
            target.start()
            self.addCleanup(target.stop)


def start_login(client, phone=PHONE):
    return client.post(reverse("accounts:login"), {"phone_number": phone})


@override_settings(**SIMULATION)
class SimulatedBackendTests(OfflineMixin, TestCase):
    def test_hors_ligne_succes_et_identifiant_coherent(self):
        with self.assertLogs("accounts.sms", "INFO") as logs:
            message_id = ConsoleSMSBackend().send(FULL_PHONE, "ACCIMAP : votre code de vérification est 483921.")
        self.assertRegex(message_id, r"^sim-[0-9a-f]{12}$")   # comme Brevo : un identifiant de message
        text = "\n".join(logs.output)
        self.assertIn("MODE DÉVELOPPEMENT - SMS SIMULÉ", text)
        self.assertIn("483921", text)                          # le code est visible (mode développement)

    def test_numero_masque_jamais_complet(self):
        with self.assertLogs("accounts.sms", "INFO") as logs:
            ConsoleSMSBackend().send(FULL_PHONE, "x")
        text = "\n".join(logs.output)
        self.assertIn(MASKED, text)
        self.assertNotIn(FULL_PHONE, text)
        self.assertNotIn("90123456", text)

    def test_selection_automatique(self):
        self.assertIsInstance(get_sms_backend(), ConsoleSMSBackend)
        with override_settings(SMS_BACKEND="accounts.sms.BrevoSMSBackend"):
            self.assertIsInstance(get_sms_backend(), ConsoleSMSBackend)   # dev : jamais de SMS réel

    def test_aucune_cle_brevo_necessaire(self):
        self.assertEqual(settings.SMS_API_KEY, "")
        self.assertTrue(request_otp(PHONE).dev_code)


@override_settings(**SIMULATION)
class SimulatedOTPCycleTests(OfflineMixin, TestCase):
    """Le système OTP existant, inchangé, avec le transport simulé."""

    def sent_code(self, logs):
        return re.search(r"\b(\d{6})\b", "\n".join(logs.output)).group(1)

    def test_code_genere_hache_et_identique_au_sms_simule(self):
        with self.assertLogs("accounts.sms", "INFO") as logs:
            result = request_otp(PHONE)
        self.assertEqual(self.sent_code(logs), result.dev_code)
        stored = OTPCode.objects.get()
        self.assertEqual(len(stored.code_hash), 64)
        self.assertNotIn(result.dev_code, stored.code_hash)    # jamais stocké en clair

    def test_mauvais_code_refuse_bon_code_accepte(self):
        result = request_otp(PHONE)
        wrong = "000000" if result.dev_code != "000000" else "111111"
        with self.assertRaises(OTPInvalid):
            verify_otp(PHONE, wrong)
        self.assertEqual(OTPCode.objects.get().attempts, 1)
        self.assertEqual(verify_otp(PHONE, result.dev_code).phone_number, FULL_PHONE)

    @override_settings(OTP_MAX_ATTEMPTS=3)
    def test_tentatives_maximales(self):
        result = request_otp(PHONE)
        wrong = "000000" if result.dev_code != "000000" else "111111"
        for _ in range(3):
            with self.assertRaises(OTPInvalid):
                verify_otp(PHONE, wrong)
        with self.assertRaises(OTPTooManyAttempts):
            verify_otp(PHONE, result.dev_code)               # même le bon code est refusé ensuite

    def test_expiration(self):
        start_login(self.client)
        code = self.client.session["otp_dev_code"]
        OTPCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        page = self.client.post(reverse("accounts:verify_otp"), {"code": code})
        self.assertContains(page, "Le code OTP a expiré")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(OTP_RESEND_DELAY_SECONDS=60)
    def test_delai_de_renvoi(self):
        start_login(self.client)
        self.client.post(reverse("accounts:resend_otp"))
        self.assertEqual(OTPCode.objects.count(), 1)          # pas de second « SMS » avant 60 s

    @override_settings(OTP_MAX_REQUESTS_PER_HOUR=2)
    def test_plafond_horaire(self):
        start_login(self.client)
        start_login(self.client)
        page = start_login(self.client)
        self.assertContains(page, "Trop de codes demandés")
        self.assertEqual(OTPCode.objects.count(), 2)

    def test_parcours_navigateur_complet_et_connexion(self):
        with self.assertLogs("accounts.sms", "INFO") as logs:
            response = start_login(self.client)
        self.assertRedirects(response, reverse("accounts:verify_otp"))
        page = self.client.get(reverse("accounts:verify_otp"))
        code = self.sent_code(logs)
        for expected in ("MODE DÉVELOPPEMENT", "SMS simulé", "Aucun SMS réel n'est envoyé", MASKED, code,
                         "visible uniquement en mode simulation"):
            self.assertContains(page, expected)
        self.assertNotContains(page, FULL_PHONE)
        self.assertContains(self.client.post(reverse("accounts:verify_otp"), {"code": "000000" if code != "000000"
                                                                               else "111111"}),
                            "Le code OTP est incorrect.")
        done = self.client.post(reverse("accounts:verify_otp"), {"code": code}, follow=True)
        user = User.objects.get(phone_number=FULL_PHONE)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertContains(done, "Connexion réussie")
        self.assertFalse(user.is_staff or user.has_usable_password())

    def test_aucun_contournement_par_parametre(self):
        start_login(self.client)
        for query in ("?otp=123456", "?debug_otp=true", "?code=123456"):
            self.client.get(reverse("accounts:verify_otp") + query)
            self.client.post(reverse("accounts:verify_otp") + query, {})
        self.assertNotIn("_auth_user_id", self.client.session)


@override_settings(OTP_DEV_MODE=False, SMS_BACKEND="accounts.sms.ConsoleSMSBackend", SMS_API_KEY="")
class SimulationDisabledInProductionTests(OfflineMixin, TestCase):
    def test_backend_simule_refuse_meme_appele_directement(self):
        with self.assertRaisesMessage(SMSError, "OTP_DEV_MODE=False"):
            ConsoleSMSBackend().send(FULL_PHONE, "ACCIMAP : votre code est 123456.")
        with self.assertRaises(SMSError):
            get_sms_backend()

    def test_aucun_code_expose_ni_journalise(self):
        with self.assertLogs("accounts", "INFO") as logs:
            with self.assertRaises(OTPDeliveryError):
                request_otp(PHONE)
        self.assertEqual(OTPCode.objects.count(), 0)
        self.assertNotRegex("\n".join(logs.output), r"\b\d{6}\b")
        self.assertNotIn(FULL_PHONE, "\n".join(logs.output))
        page = start_login(self.client)
        self.assertNotIn("otp_dev_code", self.client.session)
        self.assertNotContains(page, "SMS simulé")
        self.assertNotContains(page, "MODE DÉVELOPPEMENT")


class ConfigurationChecksTests(TestCase):
    def ids(self):
        return {m.id for m in run_checks() if m.id.startswith("accounts.")}

    @override_settings(OTP_DEV_MODE=True, DEBUG=True, SMS_BACKEND="accounts.sms.ConsoleSMSBackend", SMS_API_KEY="")
    def test_simulation_correcte_sans_cle(self):
        self.assertEqual(self.ids(), set())

    @override_settings(OTP_DEV_MODE=True, SMS_BACKEND="accounts.sms.Inexistant")
    def test_backend_inexistant(self):
        self.assertEqual(self.ids(), {"accounts.W001"})

    @override_settings(OTP_DEV_MODE=True, SMS_BACKEND="accounts.phone.mask_phone")
    def test_backend_qui_n_est_pas_un_fournisseur(self):
        self.assertEqual(self.ids(), {"accounts.W001"})

    @override_settings(OTP_DEV_MODE=True, DEBUG=True, SMS_BACKEND="accounts.sms.BrevoSMSBackend")
    def test_incoherence_brevo_en_mode_simulation(self):
        self.assertEqual(self.ids(), {"accounts.W005"})

    @override_settings(OTP_DEV_MODE=True, DEBUG=False, SMS_BACKEND="accounts.sms.ConsoleSMSBackend")
    def test_simulation_sur_serveur_public(self):
        self.assertEqual(self.ids(), {"accounts.W006"})


@override_settings(**SIMULATION)
class SmsTestCommandSimulationTests(OfflineMixin, TestCase):
    """« manage.py sms_test » en simulation : sans Brevo, sans clé, sans Internet, sans saisie."""

    def test_account_sans_cle_ni_reseau(self):
        out = io.StringIO()
        call_command("sms_test", "--account", stdout=out)
        self.assertIn("SMS SIMULÉ", out.getvalue())
        self.assertIn("Configuration de simulation : OK.", out.getvalue())

    def test_cycle_complet_simule(self):
        out = io.StringIO()
        with patch("builtins.input", side_effect=AssertionError("aucune saisie en simulation")):
            call_command("sms_test", "+22890123456", stdout=out)
        text = out.getvalue()
        for step in ("Génération", "Envoi simulé", "destinataire masqué", "Réception", "mauvais code est refusé",
                     "bon code accepté", "session ouverte", "Cycle OTP complet (simulé) : OK."):
            self.assertIn(step, text)
        self.assertNotIn(FULL_PHONE, text)
        self.assertFalse(User.objects.filter(phone_number=FULL_PHONE).exists())   # compte de test supprimé
        self.assertFalse(OTPCode.objects.exists())

    def test_keep_user(self):
        call_command("sms_test", "+22890123456", "--keep-user", stdout=io.StringIO())
        self.assertTrue(User.objects.filter(phone_number=FULL_PHONE).exists())

    def test_compte_existant_conserve(self):
        User.objects.create_user(PHONE)
        call_command("sms_test", "+22890123456", stdout=io.StringIO())
        self.assertTrue(User.objects.filter(phone_number=FULL_PHONE).exists())

    def test_numero_invalide(self):
        with self.assertRaisesMessage(CommandError, "Numéro invalide"):
            call_command("sms_test", "abc", stdout=io.StringIO())

    @override_settings(OTP_RESEND_DELAY_SECONDS=60)
    def test_delai_de_renvoi_respecte_par_la_commande(self):
        call_command("sms_test", "+22890123456", "--keep-user", stdout=io.StringIO())
        with self.assertRaisesMessage(CommandError, "délai de renvoi"):
            call_command("sms_test", "+22890123456", stdout=io.StringIO())

    @override_settings(OTP_DEV_MODE=False, SMS_BACKEND="accounts.sms.BrevoSMSBackend", SMS_API_KEY="")
    def test_mode_reel_sans_cle_erreur_explicite(self):
        with self.assertRaisesMessage(CommandError, "SMS_API_KEY"):
            call_command("sms_test", "+22890123456", stdout=io.StringIO())

    @override_settings(OTP_DEV_MODE=False, SMS_BACKEND="accounts.sms.ConsoleSMSBackend")
    def test_mode_reel_avec_backend_simule_erreur_explicite(self):
        with self.assertRaisesMessage(CommandError, "backend simulé"):
            call_command("sms_test", "+22890123456", stdout=io.StringIO())
