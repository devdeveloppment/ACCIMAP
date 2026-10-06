"""Contrôles de configuration SMS (``python manage.py check``) : avertissements, jamais bloquants."""
import re

from django.conf import settings
from django.core.checks import Warning, register
from django.utils.module_loading import import_string


@register()
def sms_configuration(app_configs, **kwargs):
    try:
        backend = import_string(settings.SMS_BACKEND)
    except ImportError:
        return [Warning(f"SMS_BACKEND introuvable : {settings.SMS_BACKEND}.",
                        hint="Valeurs possibles : accounts.sms.ConsoleSMSBackend (simulation) ou "
                             "accounts.sms.BrevoSMSBackend (réel).", id="accounts.W001")]

    from .sms import BaseSMSBackend, BrevoSMSBackend, ConsoleSMSBackend

    if not (isinstance(backend, type) and issubclass(backend, BaseSMSBackend)):
        return [Warning(f"SMS_BACKEND ne désigne pas un fournisseur SMS : {settings.SMS_BACKEND}.",
                        hint="La classe doit hériter de accounts.sms.BaseSMSBackend.", id="accounts.W001")]

    if settings.OTP_DEV_MODE:
        # Mode simulation : aucun SMS réel, aucune clé requise. On signale seulement les incohérences.
        issues = []
        if not issubclass(backend, ConsoleSMSBackend):
            issues.append(Warning(
                f"OTP_DEV_MODE=True : les SMS restent SIMULÉS, SMS_BACKEND ({backend.__name__}) est ignoré.",
                hint="Simulation : SMS_BACKEND=accounts.sms.ConsoleSMSBackend. "
                     "SMS réels : OTP_DEV_MODE=False.", id="accounts.W005"))
        if not settings.DEBUG:
            issues.append(Warning(
                "OTP_DEV_MODE=True avec DEBUG=False : les codes OTP sont affichés à l'écran (SMS simulé). "
                "Acceptable pour une démonstration, jamais pour une utilisation réelle.",
                hint="Production réelle : OTP_DEV_MODE=False et un fournisseur SMS configuré.", id="accounts.W006"))
        return issues

    if issubclass(backend, ConsoleSMSBackend):
        return [Warning("OTP_DEV_MODE=False avec le backend simulé : aucun SMS ne pourra être envoyé.",
                        hint="SMS_BACKEND=accounts.sms.BrevoSMSBackend, ou OTP_DEV_MODE=True pour une démonstration.",
                        id="accounts.W002")]

    issues = []
    if issubclass(backend, BrevoSMSBackend):
        if not settings.SMS_API_KEY:
            issues.append(Warning("SMS_API_KEY (clé API Brevo) n'est pas définie : la connexion par OTP échouera.",
                                  hint="Brevo > Paramètres > SMTP & API > Clés API.", id="accounts.W003"))
        sender = settings.SMS_SENDER_ID or ""
        if not (re.fullmatch(r"[A-Za-z0-9 ]{1,11}", sender) or re.fullmatch(r"\d{1,15}", sender)):
            issues.append(Warning(f"SMS_SENDER_ID invalide pour Brevo : {sender!r}.",
                                  hint="11 caractères alphanumériques ou 15 chiffres au maximum.", id="accounts.W004"))
    return issues
