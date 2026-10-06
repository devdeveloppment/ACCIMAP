from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import OTPCode, User
from accounts.otp import (
    AccountDisabled,
    OTPDeliveryError,
    OTPExpired,
    OTPInvalid,
    OTPRateLimited,
    OTPTooManyAttempts,
    request_otp,
    verify_otp,
)
from accounts.sms import BaseSMSBackend

PHONE = "90123456"
FULL_PHONE = "+22890123456"


class FakeSMSBackend(BaseSMSBackend):
    """Fournisseur factice : mémorise les messages envoyés."""

    sent = []

    def send(self, phone_number, message):
        FakeSMSBackend.sent.append((phone_number, message))


@override_settings(OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=0, OTP_MAX_ATTEMPTS=3)
class OTPDevModeTests(TestCase):
    def test_request_cree_un_code_hache_et_ecrit_en_console(self):
        with self.assertLogs("accounts.sms", level="INFO") as logs:
            result = request_otp(PHONE)
        self.assertEqual(result.phone_number, FULL_PHONE)
        self.assertEqual(len(result.dev_code), 6)
        self.assertIn(result.dev_code, logs.output[0])
        stored = OTPCode.objects.get()
        self.assertNotIn(result.dev_code, stored.code_hash)  # jamais stocké en clair
        self.assertEqual(len(stored.code_hash), 64)

    def test_verification_reussie_cree_l_utilisateur(self):
        result = request_otp(PHONE)
        user = verify_otp(PHONE, result.dev_code)
        self.assertEqual(user.phone_number, FULL_PHONE)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(OTPCode.objects.get().is_used)

    def test_utilisateur_existant_est_reutilise(self):
        existing = User.objects.create_user(PHONE)
        result = request_otp(PHONE)
        self.assertEqual(verify_otp(PHONE, result.dev_code), existing)
        self.assertEqual(User.objects.count(), 1)

    def test_code_usage_unique(self):
        result = request_otp(PHONE)
        verify_otp(PHONE, result.dev_code)
        with self.assertRaises(OTPExpired):
            verify_otp(PHONE, result.dev_code)

    def test_code_incorrect_incremente_les_tentatives(self):
        request_otp(PHONE)
        with self.assertRaisesMessage(OTPInvalid, "Le code OTP est incorrect."):
            verify_otp(PHONE, "000000")
        # Le compteur doit survivre à l'exception (pas de rollback).
        self.assertEqual(OTPCode.objects.get().attempts, 1)

    def test_trop_de_tentatives_bloque_meme_avec_le_bon_code(self):
        result = request_otp(PHONE)
        wrong = "000000" if result.dev_code != "000000" else "111111"
        for _ in range(3):
            with self.assertRaises(OTPInvalid):
                verify_otp(PHONE, wrong)
        with self.assertRaises(OTPTooManyAttempts):
            verify_otp(PHONE, result.dev_code)

    def test_code_expire(self):
        result = request_otp(PHONE)
        OTPCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaisesMessage(OTPExpired, "expiré"):
            verify_otp(PHONE, result.dev_code)

    def test_aucun_code_demande(self):
        with self.assertRaises(OTPExpired):
            verify_otp(PHONE, "123456")

    def test_nouveau_code_invalide_le_precedent(self):
        first = request_otp(PHONE)
        second = request_otp(PHONE)
        if first.dev_code != second.dev_code:
            with self.assertRaises(OTPInvalid):
                verify_otp(PHONE, first.dev_code)
        self.assertEqual(OTPCode.objects.filter(is_used=False).count(), 1)

    def test_numero_invalide(self):
        with self.assertRaises(ValidationError):
            request_otp("abc")
        with self.assertRaises(ValidationError):
            verify_otp("abc", "123456")

    def test_compte_desactive(self):
        User.objects.create_user(PHONE, is_active=False)
        result = request_otp(PHONE)
        with self.assertRaises(AccountDisabled):
            verify_otp(PHONE, result.dev_code)

    @override_settings(OTP_MAX_REQUESTS_PER_HOUR=2)
    def test_plafond_horaire(self):
        request_otp(PHONE)
        request_otp(PHONE)
        with self.assertRaises(OTPRateLimited):
            request_otp(PHONE)


@override_settings(OTP_DEV_MODE=True, OTP_RESEND_DELAY_SECONDS=60)
class OTPResendDelayTests(TestCase):
    def test_renvoi_trop_rapproche(self):
        request_otp(PHONE)
        with self.assertRaises(OTPRateLimited) as ctx:
            request_otp(PHONE)
        self.assertGreater(ctx.exception.wait_seconds, 0)
        self.assertEqual(OTPCode.objects.count(), 1)


@override_settings(OTP_DEV_MODE=False, OTP_RESEND_DELAY_SECONDS=0)
class OTPProductionModeTests(TestCase):
    def setUp(self):
        FakeSMSBackend.sent = []

    @override_settings(SMS_BACKEND="accounts.tests.test_otp.FakeSMSBackend")
    def test_envoi_via_le_fournisseur_et_aucun_code_expose(self):
        result = request_otp(PHONE)
        self.assertIsNone(result.dev_code)  # jamais exposé hors mode dev
        self.assertEqual(len(FakeSMSBackend.sent), 1)
        phone, message = FakeSMSBackend.sent[0]
        self.assertEqual(phone, FULL_PHONE)
        self.assertRegex(message, r"\b\d{6}\b")

    @override_settings(SMS_BACKEND="accounts.sms.ConsoleSMSBackend")
    def test_sans_fournisseur_l_envoi_est_refuse_et_rien_n_est_enregistre(self):
        with self.assertRaises(OTPDeliveryError):
            request_otp(PHONE)
        self.assertEqual(OTPCode.objects.count(), 0)

    @override_settings(SMS_BACKEND="inexistant.module.Classe")
    def test_fournisseur_introuvable(self):
        with self.assertRaises(OTPDeliveryError):
            request_otp(PHONE)
