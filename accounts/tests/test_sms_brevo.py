"""
Fournisseur SMS Brevo : requête envoyée à l'API officielle, gestion des erreurs, sélection automatique, contrôles.
L'API Brevo est SIMULÉE (aucun SMS réel, aucun coût) ; le vrai test se fait avec ``manage.py sms_test``.
"""
import io
import json
import re
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.core.checks import run_checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import OTPCode, User
from accounts.otp import OTPDeliveryError, request_otp
from accounts.sms import BrevoSMSBackend, ConsoleSMSBackend, SMSError, get_sms_backend

KEY = "xkeysib-cle-de-test-non-reelle"
PHONE = "+22890123456"
BREVO = "accounts.sms.urlopen"


class FakeResponse(io.BytesIO):
    def __init__(self, body, status=201):
        super().__init__(json.dumps(body).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def brevo_ok(sent):
    """urlopen simulé : mémorise chaque requête et répond comme Brevo (201 + messageId)."""
    def fake(request, timeout):
        sent.append({"url": request.full_url, "method": request.get_method(), "timeout": timeout,
                     "headers": {k.lower(): v for k, v in request.header_items()},
                     "body": json.loads(request.data.decode("utf-8"))})
        return FakeResponse({"messageId": 1511882900100020})
    return fake


def http_error(status, body):
    return HTTPError("https://api.brevo.com/v3/transactionalSMS/send", status, "Erreur", {},
                     io.BytesIO(json.dumps(body).encode("utf-8")))


@override_settings(SMS_API_KEY=KEY, SMS_SENDER_ID="ACCIMAP", OTP_DEV_MODE=False,
                   SMS_BACKEND="accounts.sms.BrevoSMSBackend")
class BrevoBackendTests(TestCase):
    def test_requete_conforme_a_l_api_officielle(self):
        sent = []
        with patch(BREVO, side_effect=brevo_ok(sent)):
            message_id = BrevoSMSBackend().send(PHONE, "ACCIMAP : votre code de vérification est 123456.")
        self.assertEqual(message_id, 1511882900100020)
        request = sent[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], "https://api.brevo.com/v3/transactionalSMS/send")
        self.assertEqual(request["headers"]["api-key"], KEY)
        self.assertEqual(request["headers"]["content-type"], "application/json")
        self.assertEqual(request["timeout"], settings.SMS_TIMEOUT_SECONDS)
        self.assertEqual(request["body"], {
            "sender": "ACCIMAP", "recipient": "22890123456", "type": "transactional", "tag": "accimap-otp",
            "content": "ACCIMAP : votre code de vérification est 123456.",
        })   # é est dans l'alphabet GSM : pas d'Unicode (160 caractères par SMS)

    def test_unicode_active_si_caracteres_hors_alphabet_gsm(self):
        sent = []
        with patch(BREVO, side_effect=brevo_ok(sent)):
            BrevoSMSBackend().send(PHONE, "Code : 123456 — merci")
        self.assertTrue(sent[0]["body"]["unicodeEnabled"])

    def test_cle_absente(self):
        with override_settings(SMS_API_KEY=""), patch(BREVO) as urlopen:
            with self.assertRaisesMessage(SMSError, "SMS_API_KEY"):
                BrevoSMSBackend().send(PHONE, "x")
        urlopen.assert_not_called()

    def test_erreurs_brevo_explicites_sans_fuite_de_secret(self):
        cases = [
            (401, {"code": "unauthorized", "message": "Key not found"}, "HTTP 401"),
            (402, {"code": "not_enough_credits", "message": "Not enough credits"}, "not_enough_credits"),
            (400, {"code": "invalid_parameter", "message": "Invalid sender"}, "Invalid sender"),
        ]
        for status, body, expected in cases:
            with self.subTest(status=status), patch(BREVO, side_effect=http_error(status, body)):
                with self.assertRaises(SMSError) as ctx:
                    BrevoSMSBackend().send(PHONE, "ACCIMAP : votre code est 654321.")
                self.assertIn(expected, str(ctx.exception))
                self.assertNotIn(KEY, str(ctx.exception))
                self.assertNotIn("654321", str(ctx.exception))

    def test_reseau_indisponible(self):
        for error in (URLError("down"), TimeoutError()):
            with self.subTest(error=error), patch(BREVO, side_effect=error):
                with self.assertRaisesMessage(SMSError, "Brevo injoignable"):
                    BrevoSMSBackend().send(PHONE, "x")

    def test_journal_sans_code_ni_cle_ni_numero_complet(self):
        with patch(BREVO, side_effect=brevo_ok([])), self.assertLogs("accounts.sms", "INFO") as logs:
            BrevoSMSBackend().send(PHONE, "ACCIMAP : votre code est 777888.")
        text = "\n".join(logs.output)
        self.assertIn("+228****56", text)
        for secret in ("777888", KEY, PHONE):
            self.assertNotIn(secret, text)


class SelectionTests(TestCase):
    @override_settings(OTP_DEV_MODE=True, SMS_BACKEND="accounts.sms.BrevoSMSBackend", SMS_API_KEY=KEY)
    def test_mode_dev_reste_en_console_meme_avec_brevo_configure(self):
        self.assertIsInstance(get_sms_backend(), ConsoleSMSBackend)

    @override_settings(OTP_DEV_MODE=False, SMS_BACKEND="accounts.sms.BrevoSMSBackend", SMS_API_KEY=KEY)
    def test_mode_production_selectionne_brevo(self):
        self.assertIsInstance(get_sms_backend(), BrevoSMSBackend)

    def test_les_tests_ne_peuvent_jamais_envoyer_de_vrai_sms(self):
        self.assertEqual(settings.SMS_API_KEY, "")   # garde-fou de config/settings.py pendant « manage.py test »


@override_settings(OTP_DEV_MODE=False, OTP_RESEND_DELAY_SECONDS=0, SMS_API_KEY=KEY,
                   SMS_BACKEND="accounts.sms.BrevoSMSBackend")
class OTPWithBrevoTests(TestCase):
    """Service OTP de bout en bout avec Brevo simulé."""

    def test_code_envoye_par_brevo_et_jamais_expose(self):
        sent = []
        with patch(BREVO, side_effect=brevo_ok(sent)):
            result = request_otp("90123456")
        self.assertIsNone(result.dev_code)
        self.assertEqual(sent[0]["body"]["recipient"], "22890123456")
        code = re.search(r"\b(\d{6})\b", sent[0]["body"]["content"]).group(1)
        self.assertNotIn(code, OTPCode.objects.get().code_hash)

    def test_echec_brevo_aucun_code_enregistre_et_message_utilisateur(self):
        with patch(BREVO, side_effect=http_error(402, {"code": "not_enough_credits", "message": "x"})):
            with self.assertRaises(OTPDeliveryError):
                request_otp("90123456")
        self.assertEqual(OTPCode.objects.count(), 0)
        with patch(BREVO, side_effect=URLError("down")):
            page = self.client.post("/login/", {"phone_number": "90123456"})
        self.assertContains(page, "Le SMS n&#x27;a pas pu être envoyé")


class SMSChecksTests(TestCase):
    def ids(self):
        return {m.id for m in run_checks() if m.id.startswith("accounts.")}

    @override_settings(OTP_DEV_MODE=True, SMS_API_KEY="", DEBUG=True, SMS_BACKEND="accounts.sms.ConsoleSMSBackend")
    def test_mode_dev_aucun_avertissement(self):
        self.assertEqual(self.ids(), set())

    @override_settings(OTP_DEV_MODE=False, SMS_BACKEND="accounts.sms.BrevoSMSBackend", SMS_API_KEY="")
    def test_production_sans_cle(self):
        self.assertIn("accounts.W003", self.ids())

    @override_settings(OTP_DEV_MODE=False, SMS_BACKEND="accounts.sms.BrevoSMSBackend", SMS_API_KEY=KEY,
                       SMS_SENDER_ID="ACCIMAP-TOGO-OTP")
    def test_sender_id_trop_long(self):
        self.assertEqual(self.ids(), {"accounts.W004"})

    @override_settings(OTP_DEV_MODE=False, SMS_BACKEND="accounts.sms.ConsoleSMSBackend")
    def test_production_avec_console(self):
        self.assertEqual(self.ids(), {"accounts.W002"})

    @override_settings(OTP_DEV_MODE=False, SMS_BACKEND="accounts.sms.BrevoSMSBackend", SMS_API_KEY=KEY)
    def test_production_correcte(self):
        self.assertEqual(self.ids(), set())


@override_settings(SMS_API_KEY=KEY, OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=0)
class SmsTestCommandTests(TestCase):
    """La commande en mode Brevo (--brevo), avec l'API Brevo simulée et le code « reçu » saisi au clavier."""

    def test_cycle_complet(self):
        sent = []

        def typed_code(prompt):
            if "Code reçu" in prompt:
                return re.search(r"\b(\d{6})\b", sent[0]["body"]["content"]).group(1)
            return "o"

        out = io.StringIO()
        with patch(BREVO, side_effect=brevo_ok(sent)), patch("builtins.input", side_effect=typed_code):
            call_command("sms_test", "--brevo", "+22890123456", stdout=out)
        self.assertIn("Cycle OTP complet (réel) : OK", out.getvalue())
        self.assertEqual(len(sent), 1)   # OTP_DEV_MODE=True dans .env : la commande force bien le mode production
        self.assertFalse(User.objects.filter(phone_number=PHONE).exists())   # compte de test supprimé

    def test_mauvais_code(self):
        with patch(BREVO, side_effect=brevo_ok([])), patch("builtins.input", side_effect=["o", "000000"]):
            with self.assertRaisesMessage(CommandError, "Code refusé"):
                call_command("sms_test", "--brevo", "+22890123456", stdout=io.StringIO())

    def test_confirmation_refusee_aucun_envoi(self):
        with patch(BREVO) as urlopen, patch("builtins.input", return_value="n"):
            with self.assertRaisesMessage(CommandError, "Annulé"):
                call_command("sms_test", "--brevo", "+22890123456", stdout=io.StringIO())
        urlopen.assert_not_called()

    @override_settings(SMS_API_KEY="")
    def test_sans_cle(self):
        with self.assertRaisesMessage(CommandError, "SMS_API_KEY"):
            call_command("sms_test", "--brevo", "--account")

    def test_compte_et_credits(self):
        account = {"email": "x@example.com", "plan": [{"type": "free", "credits": 300},
                                                      {"type": "sms", "credits": 100}]}
        out = io.StringIO()
        with patch("accounts.management.commands.sms_test.urlopen", return_value=FakeResponse(account, 200)) as urlopen:
            call_command("sms_test", "--brevo", "--account", stdout=out)
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://api.brevo.com/v3/account")
        self.assertIn("Crédits SMS disponibles : 100", out.getvalue())
