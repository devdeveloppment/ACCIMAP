"""
Couche d'envoi de SMS : isolée du reste de l'application.

Le reste du code appelle uniquement ``get_sms_backend().send(numero, message)``.

Fournisseurs disponibles :
  * ConsoleSMSBackend : SMS SIMULÉ (développement / soutenance, OTP_DEV_MODE=True) : aucun SMS réel, hors ligne ;
  * BrevoSMSBackend   : fournisseur réel (API officielle Brevo, SMS transactionnels), OTP_DEV_MODE=False.
    Variables : SMS_API_KEY (clé API Brevo), SMS_SENDER_ID (Sender ID approuvé pour le Togo).

Passer de l'un à l'autre ne demande que les variables d'environnement :
    simulation : SMS_BACKEND=accounts.sms.ConsoleSMSBackend  OTP_DEV_MODE=True
    production : SMS_BACKEND=accounts.sms.BrevoSMSBackend    OTP_DEV_MODE=False  SMS_API_KEY=...

BRANCHER UN AUTRE FOURNISSEUR
-----------------------------
1. Créer une classe qui hérite de ``BaseSMSBackend`` (dans ce fichier ou dans
   un nouveau module), par exemple :

       class MonFournisseurSMSBackend(BaseSMSBackend):
           def send(self, phone_number, message):
               # Appeler l'API du fournisseur avec settings.SMS_API_KEY,
               # settings.SMS_API_SECRET et settings.SMS_SENDER_ID.
               # En cas d'échec : raise SMSError("message explicite")

2. Renseigner dans les variables d'environnement (jamais dans Git) :

       SMS_BACKEND=accounts.sms.MonFournisseurSMSBackend
       SMS_API_KEY=...
       SMS_API_SECRET=...
       SMS_SENDER_ID=ACCIMAP
       OTP_DEV_MODE=False

Rien d'autre à modifier : le service OTP (accounts/otp.py) et les vues
n'ont pas connaissance du fournisseur utilisé.
"""
import json
import logging
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils.module_loading import import_string

from .phone import mask_phone

logger = logging.getLogger(__name__)

# Alphabet GSM 03.38 de base : un SMS qui n'utilise que ces caractères tient en 160 caractères ; sinon il doit être
# envoyé en Unicode (70 caractères par SMS). Le message OTP d'ACCIMAP (é, à) reste dans l'alphabet GSM.
GSM7_CHARACTERS = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)


class SMSError(Exception):
    """Échec d'envoi d'un SMS (fournisseur absent, API en erreur, etc.)."""


class BaseSMSBackend:
    """Interface commune à tous les fournisseurs."""

    def send(self, phone_number, message):
        raise NotImplementedError("Un backend SMS doit implémenter send().")


class ConsoleSMSBackend(BaseSMSBackend):
    """
    SMS SIMULÉ (développement, tests, soutenance) : seul le TRANSPORT est simulé.

    Même interface que le fournisseur réel : reçoit le même numéro et le même message, renvoie un identifiant de
    message. Aucun appel réseau, aucun crédit, aucune donnée OTP modifiée (le code reste généré, haché, expiré et
    vérifié par accounts/otp.py). Le message est écrit dans la console du serveur, numéro MASQUÉ.

    Ne fonctionne QUE si OTP_DEV_MODE=True : sinon get_sms_backend le refuse, et send() lui-même lève SMSError
    (aucun code OTP ne peut finir dans les logs de production).
    """

    def send(self, phone_number, message):
        if not settings.OTP_DEV_MODE:
            raise SMSError("SMS simulé refusé : OTP_DEV_MODE=False (configurez un fournisseur réel).")
        message_id = f"sim-{uuid.uuid4().hex[:12]}"
        rule = "-" * 48
        logger.info(
            "\n%s\nMODE DÉVELOPPEMENT - SMS SIMULÉ (aucun SMS réel envoyé)\n%s\n"
            "Destinataire : %s\nMessage      : %s\nIdentifiant  : %s\n%s",
            rule, rule, mask_phone(phone_number), message, message_id, rule,
        )
        return message_id


class BrevoSMSBackend(BaseSMSBackend):
    """
    SMS transactionnel via l'API officielle Brevo :
    POST https://api.brevo.com/v3/transactionalSMS/send, en-tête « api-key » (https://developers.brevo.com/).

    Prérequis côté compte Brevo : crédits SMS achetés et Sender ID approuvé pour le Togo.
    Ni la clé ni le contenu du message (qui contient le code) ne sont jamais journalisés.
    """

    def send(self, phone_number, message):
        if not settings.SMS_API_KEY:
            raise SMSError("SMS_API_KEY (clé API Brevo) n'est pas définie.")

        payload = {
            "sender": settings.SMS_SENDER_ID,
            "recipient": phone_number.lstrip("+"),   # Brevo : indicatif pays sans « + » (ex. 22890123456)
            "content": message,
            "type": "transactional",
            "tag": "accimap-otp",
        }
        if not set(message) <= GSM7_CHARACTERS:
            payload["unicodeEnabled"] = True
        request = Request(
            settings.SMS_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "api-key": settings.SMS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=settings.SMS_TIMEOUT_SECONDS) as response:  # noqa: S310 (URL de configuration)
                body = json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            raise SMSError(f"Brevo a refusé l'envoi (HTTP {exc.code}) : {_brevo_error(exc)}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SMSError(f"Brevo injoignable : {exc.__class__.__name__}") from exc
        except ValueError as exc:
            raise SMSError("Réponse Brevo illisible.") from exc

        logger.info("SMS envoyé via Brevo à %s (messageId=%s).", mask_phone(phone_number), body.get("messageId"))
        return body.get("messageId")


def _brevo_error(exc):
    """Code et message d'erreur renvoyés par Brevo ({"code": ..., "message": ...})."""
    try:
        data = json.loads(exc.read().decode("utf-8"))
    except (ValueError, OSError):
        return exc.reason or "erreur inconnue"
    return " - ".join(str(part) for part in (data.get("code"), data.get("message")) if part) or "erreur inconnue"


def get_sms_backend():
    """
    Retourne le backend SMS à utiliser.

    - OTP_DEV_MODE=True  -> console, quel que soit SMS_BACKEND.
    - OTP_DEV_MODE=False -> SMS_BACKEND, qui ne peut PAS être le backend console
      (sinon les codes finiraient dans les logs du serveur de production).
    """
    if settings.OTP_DEV_MODE:
        return ConsoleSMSBackend()

    try:
        backend_class = import_string(settings.SMS_BACKEND)
    except ImportError as exc:
        raise SMSError(f"Backend SMS introuvable : {settings.SMS_BACKEND}") from exc

    if issubclass(backend_class, ConsoleSMSBackend):
        raise SMSError(
            "Aucun fournisseur SMS n'est configuré. Définissez SMS_BACKEND, "
            "ou activez OTP_DEV_MODE pour une démonstration."
        )
    return backend_class()
