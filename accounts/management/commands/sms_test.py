"""
Test du cycle OTP complet, quel que soit le fournisseur SMS. Passe par les VRAIES vues (/login/, /verify-otp/) :
génération, empreinte, expiration, tentatives, renvoi et connexion sont exactement ceux de la production.

Le fournisseur suit la configuration (.env) :
  * OTP_DEV_MODE=True  -> SMS SIMULÉ (ConsoleSMSBackend) : hors ligne, sans clé ni crédit. Le code est lu là où
                          l'interface le présente en mode simulation (bandeau « Mode développement — SMS simulé »).
  * OTP_DEV_MODE=False -> fournisseur réel (SMS_BACKEND, Brevo par défaut) : un SMS RÉEL est envoyé et le code reçu
                          sur le téléphone est saisi au clavier.
  * --brevo            -> force le test réel Brevo, même si .env est en mode simulation.

    python manage.py sms_test --account               # configuration (simulation) ou clé + crédits (Brevo)
    python manage.py sms_test +228XXXXXXXX            # cycle complet avec le fournisseur configuré
    python manage.py sms_test --brevo --account       # Brevo : clé valide, crédits restants (aucun SMS envoyé)
    python manage.py sms_test --brevo +228XXXXXXXX    # Brevo : vrai SMS (1 crédit au moins)

Le compte citoyen créé pour le test est supprimé à la fin (sauf --keep-user, ou s'il existait déjà).
"""
import json
import logging
from contextlib import nullcontext
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.test import Client, override_settings
from django.utils.module_loading import import_string

from accounts.models import OTPCode, User
from accounts.phone import mask_phone, normalize_phone
from accounts.sms import BrevoSMSBackend, ConsoleSMSBackend, get_sms_backend
from accounts.views import SESSION_DEV_CODE

BREVO_PATH = "accounts.sms.BrevoSMSBackend"


class _Capture(logging.Handler):
    """Capte le message du SMS simulé (journal de accounts.sms) pour le montrer dans le rapport."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


class Command(BaseCommand):
    help = "Test du cycle OTP complet (SMS simulé ou fournisseur réel selon la configuration)."

    def add_arguments(self, parser):
        parser.add_argument("phone", nargs="?", help="Numéro de test, ex. +22890123456")
        parser.add_argument("--account", action="store_true",
                            help="Vérifier la configuration (simulation) ou la clé et les crédits (Brevo), sans envoi")
        parser.add_argument("--brevo", action="store_true", help="Forcer le fournisseur réel Brevo (SMS réel)")
        parser.add_argument("--yes", action="store_true", help="Ne pas demander de confirmation avant un envoi réel")
        parser.add_argument("--keep-user", action="store_true", help="Conserver le compte citoyen créé")

    def handle(self, *args, **options):
        simulated = settings.OTP_DEV_MODE and not options["brevo"]
        if simulated:
            self.stdout.write("Fournisseur : SMS SIMULÉ (ConsoleSMSBackend) - aucun SMS réel, aucun accès réseau.")
        else:
            backend_path = BREVO_PATH if options["brevo"] else settings.SMS_BACKEND
            self.stdout.write(f"Fournisseur : RÉEL ({backend_path.rsplit('.', 1)[-1]}).")

        if options["account"]:
            return self.check_simulation() if simulated else self.check_real(options)
        if not options["phone"]:
            raise CommandError("Indiquez un numéro de test (ex. +22890123456) ou --account.")
        try:
            phone = normalize_phone(options["phone"])
        except ValidationError as exc:
            raise CommandError(f"Numéro invalide : {options['phone']}") from exc
        if not simulated:
            self.require_real_configuration(options)
        return self.full_cycle(phone, options, simulated)

    # ------------------------------------------------------------------ configuration
    def check_simulation(self):
        backend = get_sms_backend()
        self.ok("Backend effectif : ConsoleSMSBackend (SMS simulé)", isinstance(backend, ConsoleSMSBackend))
        self.ok("Aucune clé API, aucun crédit, aucune connexion Internet nécessaires", True)
        self.stdout.write(f"  Règles OTP actives : code à {settings.OTP_LENGTH} chiffres, expiration "
                          f"{settings.OTP_EXPIRY_MINUTES} min, {settings.OTP_MAX_ATTEMPTS} tentatives, renvoi après "
                          f"{settings.OTP_RESEND_DELAY_SECONDS} s, {settings.OTP_MAX_REQUESTS_PER_HOUR} envois/heure.")
        if settings.SMS_BACKEND != f"{ConsoleSMSBackend.__module__}.{ConsoleSMSBackend.__name__}":
            self.stdout.write(self.style.WARNING(
                f"  SMS_BACKEND={settings.SMS_BACKEND} est ignoré tant que OTP_DEV_MODE=True."))
        if not settings.DEBUG:
            self.stdout.write(self.style.WARNING(
                "  DEBUG=False : codes visibles à l'écran. Démonstration uniquement, jamais en production réelle."))
        self.stdout.write(self.style.SUCCESS("Configuration de simulation : OK."))

    def require_real_configuration(self, options):
        backend_path = BREVO_PATH if options["brevo"] else settings.SMS_BACKEND
        try:
            backend = import_string(backend_path)
        except ImportError as exc:
            raise CommandError(f"SMS_BACKEND introuvable : {backend_path}") from exc
        if issubclass(backend, ConsoleSMSBackend):
            raise CommandError("OTP_DEV_MODE=False avec le backend simulé : configurez SMS_BACKEND=" + BREVO_PATH)
        if issubclass(backend, BrevoSMSBackend) and not settings.SMS_API_KEY:
            raise CommandError("SMS_API_KEY n'est pas définie dans l'environnement (.env) : requise pour Brevo.")
        return backend

    def check_real(self, options):
        backend = self.require_real_configuration(options)
        if not issubclass(backend, BrevoSMSBackend):
            self.stdout.write(f"Aucune vérification de compte disponible pour {backend.__name__}.")
            return
        parts = urlsplit(settings.SMS_API_URL)
        url = f"{parts.scheme}://{parts.netloc}/v3/account"
        request = Request(url, headers={"api-key": settings.SMS_API_KEY, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=settings.SMS_TIMEOUT_SECONDS) as response:  # noqa: S310
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise CommandError(f"Brevo refuse la clé (HTTP {exc.code}) : vérifiez SMS_API_KEY.") from exc
        except (URLError, OSError) as exc:
            raise CommandError(f"Brevo injoignable : {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Clé API Brevo valide."))
        sms_plans = [p for p in data.get("plan", []) if p.get("type") == "sms"]
        credits = sum(p.get("credits") or 0 for p in sms_plans)
        self.stdout.write(f"  Crédits SMS disponibles : {credits}")
        self.stdout.write(f"  Sender ID configuré dans ACCIMAP : {settings.SMS_SENDER_ID!r} "
                          "(doit être approuvé par Brevo pour le Togo)")
        if not credits:
            self.stdout.write(self.style.WARNING("  Aucun crédit SMS : achetez un pack dans Brevo avant le test d'envoi."))

    # ------------------------------------------------------------------ cycle complet
    def full_cycle(self, phone, options, simulated):
        self.stdout.write(f"Cycle OTP vers {mask_phone(phone)}")
        if not simulated:
            self.stdout.write(f"Sender ID : {settings.SMS_SENDER_ID!r}")
            if not options["yes"]:
                answer = input("Un SMS réel va être envoyé (1 crédit au moins). Continuer ? [o/N] ").strip().lower()
                if answer not in {"o", "oui", "y", "yes"}:
                    raise CommandError("Annulé.")

        existed = User.objects.filter(phone_number=phone).exists()
        client = Client(HTTP_HOST=(settings.ALLOWED_HOSTS or ["localhost"])[0])
        secure = not settings.DEBUG   # en production, requêtes en HTTPS (sinon redirection SECURE_SSL_REDIRECT)
        forced = override_settings(OTP_DEV_MODE=False, SMS_BACKEND=BREVO_PATH) if options["brevo"] else nullcontext()
        capture = _Capture()
        sms_logger = logging.getLogger("accounts.sms")
        previous_level = sms_logger.level
        sms_logger.setLevel(logging.INFO)     # le SMS simulé doit être capté même si LOG_LEVEL=WARNING
        sms_logger.addHandler(capture)
        try:
            with forced:
                # 1-2. Génération + envoi (vue /login/ -> request_otp -> fournisseur SMS)
                previous = OTPCode.objects.filter(phone_number=phone).values_list("pk", flat=True).first()
                response = client.post("/login/", {"phone_number": phone, "next": "/report/"}, secure=secure)
                otp = OTPCode.objects.filter(phone_number=phone).first()
                if response.status_code != 302 or "/verify-otp/" not in response.get("Location", ""):
                    raise CommandError("Échec à l'envoi : la page de connexion n'a pas redirigé vers la saisie du code "
                                       "(fournisseur ou plafond horaire : voir les messages ci-dessus).")
                if otp is None or otp.pk == previous:
                    raise CommandError("Aucun nouveau code : délai de renvoi (OTP_RESEND_DELAY_SECONDS="
                                       f"{settings.OTP_RESEND_DELAY_SECONDS}) ou plafond horaire actif pour ce numéro. "
                                       "Réessayez plus tard.")
                self.ok("Génération : code créé, seule son empreinte est en base", otp and not otp.is_used)

                # 3. Réception
                if simulated:
                    sent = "\n".join(capture.records)
                    code = client.session.get(SESSION_DEV_CODE, "")
                    self.ok("Envoi simulé : aucun appel réseau, message journalisé", "SMS SIMULÉ" in sent)
                    self.ok("Envoi simulé : destinataire masqué, jamais le numéro complet",
                            mask_phone(phone) in sent and phone not in sent)
                    self.ok("Réception : code présenté en mode simulation, identique au SMS simulé",
                            len(code) == settings.OTP_LENGTH and code in sent)
                    self.stdout.write(f"  Code OTP (simulation) : {code}")
                else:
                    self.ok("Envoi : accepté par le fournisseur (voir messageId dans les logs)", True)
                    self.ok("Aucun code exposé dans la session (mode production)", SESSION_DEV_CODE not in client.session)
                    code = input("Code reçu par SMS : ").strip()

                # 4. Validation réelle (un mauvais code d'abord en simulation : il doit être refusé)
                if simulated:
                    wrong = "0" * settings.OTP_LENGTH if code != "0" * settings.OTP_LENGTH else "1" * settings.OTP_LENGTH
                    refused = client.post("/verify-otp/", {"code": wrong}, secure=secure)
                    otp.refresh_from_db()
                    self.ok("Validation : un mauvais code est refusé (tentative comptée)",
                            refused.status_code == 200 and otp.attempts == 1 and "_auth_user_id" not in client.session)
                response = client.post("/verify-otp/", {"code": code}, secure=secure)
                if response.status_code != 302:
                    raise CommandError("Code refusé par ACCIMAP (incorrect ou expiré).")
                otp.refresh_from_db()
                self.ok("Validation : bon code accepté, code consommé (usage unique)", otp.is_used)
        finally:
            sms_logger.removeHandler(capture)
            sms_logger.setLevel(previous_level)

        # 5. Connexion réelle
        user = User.objects.get(phone_number=phone)
        self.ok("Connexion : session ouverte pour ce compte", client.session.get("_auth_user_id") == str(user.pk))
        self.ok("Connexion : accès au formulaire de signalement", client.get("/report/", secure=secure).status_code == 200)
        self.ok("Compte citoyen sans accès staff ni mot de passe", not user.is_staff and not user.has_usable_password())

        if not existed and not options["keep_user"]:
            user.delete()
            OTPCode.objects.filter(phone_number=phone).delete()
            self.stdout.write("Compte de test supprimé.")
        label = "simulé" if simulated else "réel"
        self.stdout.write(self.style.SUCCESS(f"Cycle OTP complet ({label}) : OK."))

    def ok(self, label, condition):
        if not condition:
            raise CommandError(f"ÉCHEC : {label}")
        self.stdout.write(self.style.SUCCESS("  OK ") + label)
