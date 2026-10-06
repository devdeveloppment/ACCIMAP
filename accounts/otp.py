"""
Service OTP : génération, envoi et vérification des codes à usage unique.

Les vues n'appellent que deux fonctions :
    request_otp(telephone)        -> envoie un code
    verify_otp(telephone, code)   -> retourne l'utilisateur (créé si nouveau)

Toutes les erreurs « métier » sont des sous-classes d'OTPError ; leur texte est
destiné à être affiché tel quel à l'utilisateur.
"""
import logging
import math
import secrets
import string
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import OTPCode, User
from .phone import mask_phone, normalize_phone
from .sms import SMSError, get_sms_backend

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Erreurs métier
# --------------------------------------------------------------------------
class OTPError(Exception):
    default_message = "Erreur lors de la vérification du code."

    def __init__(self, message=None):
        super().__init__(message or self.default_message)


class OTPInvalid(OTPError):
    default_message = "Le code OTP est incorrect."


class OTPExpired(OTPError):
    default_message = "Le code OTP a expiré. Demandez un nouveau code."


class OTPTooManyAttempts(OTPError):
    default_message = "Trop de tentatives incorrectes. Demandez un nouveau code."


class OTPRateLimited(OTPError):
    """Renvoi trop rapproché ou trop d'envois sur la dernière heure."""

    def __init__(self, message, wait_seconds=0):
        super().__init__(message)
        self.wait_seconds = wait_seconds


class OTPResendTooSoon(OTPRateLimited):
    """Un code vient d'être envoyé : l'ancien reste valable, il faut patienter."""


class OTPHourlyLimit(OTPRateLimited):
    """Plafond d'envois par heure atteint pour ce numéro."""


class OTPDeliveryError(OTPError):
    default_message = "Le SMS n'a pas pu être envoyé. Réessayez dans quelques instants."


class AccountDisabled(OTPError):
    default_message = "Ce compte a été désactivé. Contactez l'administrateur."


# --------------------------------------------------------------------------
# Outils internes
# --------------------------------------------------------------------------
def _generate_code():
    return "".join(secrets.choice(string.digits) for _ in range(settings.OTP_LENGTH))


def _hash_code(phone_number, code):
    """Empreinte du code (liée au numéro et à SECRET_KEY) : le clair n'est jamais stocké."""
    return salted_hmac("accimap.otp", f"{phone_number}:{code}", algorithm="sha256").hexdigest()


@dataclass
class OTPRequestResult:
    phone_number: str
    expires_at: object
    # Rempli UNIQUEMENT en OTP_DEV_MODE, pour permettre les tests sans SMS réel.
    dev_code: str | None = None


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------
def request_otp(raw_phone):
    """
    Génère un code, invalide les précédents et l'envoie par SMS.

    Lève ValidationError (numéro invalide), OTPResendTooSoon, OTPHourlyLimit
    ou OTPDeliveryError.
    """
    phone = normalize_phone(raw_phone)
    now = timezone.now()
    history = OTPCode.objects.filter(phone_number=phone)

    last = history.first()
    if last:
        wait = settings.OTP_RESEND_DELAY_SECONDS - (now - last.created_at).total_seconds()
        if wait > 0:
            seconds = math.ceil(wait)
            raise OTPResendTooSoon(
                f"Veuillez patienter {seconds} seconde(s) avant de demander un nouveau code.",
                wait_seconds=seconds,
            )

    recent_count = history.filter(created_at__gte=now - timedelta(hours=1)).count()
    if recent_count >= settings.OTP_MAX_REQUESTS_PER_HOUR:
        raise OTPHourlyLimit(
            "Trop de codes demandés. Réessayez dans une heure.", wait_seconds=3600
        )

    code = _generate_code()
    expires_at = now + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
    message = (
        f"ACCIMAP : votre code de vérification est {code}. "
        f"Il expire dans {settings.OTP_EXPIRY_MINUTES} minutes. Ne le communiquez à personne."
    )

    try:
        # Si l'envoi échoue, l'exception annule aussi la création du code.
        with transaction.atomic():
            OTPCode.objects.filter(phone_number=phone, is_used=False).update(is_used=True)
            OTPCode.objects.create(
                phone_number=phone, code_hash=_hash_code(phone, code), expires_at=expires_at
            )
            get_sms_backend().send(phone, message)
    except SMSError as exc:
        logger.error("Échec d'envoi OTP vers %s : %s", mask_phone(phone), exc)
        raise OTPDeliveryError() from exc

    return OTPRequestResult(
        phone_number=phone,
        expires_at=expires_at,
        dev_code=code if settings.OTP_DEV_MODE else None,
    )


def verify_otp(raw_phone, code):
    """
    Vérifie le code. Retourne l'utilisateur (créé à la première connexion).

    Lève ValidationError, OTPInvalid, OTPExpired, OTPTooManyAttempts ou AccountDisabled.
    """
    phone = normalize_phone(raw_phone)
    code = str(code or "").strip()
    error = None

    # L'erreur est levée APRÈS le bloc atomique : sinon l'incrément du compteur
    # de tentatives serait annulé par le rollback.
    with transaction.atomic():
        otp = (
            OTPCode.objects.select_for_update()
            .filter(phone_number=phone, is_used=False)
            .order_by("-created_at")
            .first()
        )
        if otp is None or otp.is_expired:
            error = OTPExpired()
        elif otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            error = OTPTooManyAttempts()
        elif not constant_time_compare(otp.code_hash, _hash_code(phone, code)):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            error = OTPInvalid()
        else:
            otp.is_used = True
            otp.save(update_fields=["is_used"])

    if error:
        raise error

    try:
        user = User.objects.get(phone_number=phone)
    except User.DoesNotExist:
        try:
            user = User.objects.create_user(phone)
        except IntegrityError:  # création simultanée
            user = User.objects.get(phone_number=phone)

    if not user.is_active:
        raise AccountDisabled()
    return user
