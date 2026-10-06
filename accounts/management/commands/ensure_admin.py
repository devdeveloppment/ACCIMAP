"""
Crée le compte SUPERUTILISATEUR initial à partir de variables d'environnement (hébergement sans terminal, ex. Render).

    ADMIN_PHONE=90000001  ADMIN_PASSWORD=...   python manage.py ensure_admin

Règles :
  * sans ADMIN_PHONE ni ADMIN_PASSWORD : ne fait rien (démarrage normal) ;
  * le compte n'existe pas : il est créé (superutilisateur, mot de passe validé par les règles de robustesse) ;
  * le compte existe déjà : rien n'est modifié (un mot de passe changé depuis le tableau de bord est conservé),
    sauf --reset-password, à utiliser ponctuellement en cas de mot de passe perdu ;
  * le mot de passe n'est jamais affiché ni journalisé.
"""
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from accounts.phone import mask_phone, normalize_phone
from accounts.roles import ROLE_SUPERUSER, set_role


class Command(BaseCommand):
    help = "Crée le superutilisateur initial depuis ADMIN_PHONE et ADMIN_PASSWORD (aucune modification s'il existe)."

    def add_arguments(self, parser):
        parser.add_argument("--reset-password", action="store_true",
                            help="Remplace le mot de passe du compte existant par ADMIN_PASSWORD.")

    def handle(self, *args, **options):
        raw_phone = os.getenv("ADMIN_PHONE", "").strip()
        password = os.getenv("ADMIN_PASSWORD", "")
        if not raw_phone and not password:
            self.stdout.write("ensure_admin : ADMIN_PHONE / ADMIN_PASSWORD non définis, aucun compte créé.")
            return
        if not raw_phone or not password:
            raise CommandError("ensure_admin : ADMIN_PHONE et ADMIN_PASSWORD doivent être définis ensemble.")
        try:
            phone = normalize_phone(raw_phone)
        except ValidationError as exc:
            raise CommandError(f"ensure_admin : ADMIN_PHONE invalide ({raw_phone!r}).") from exc

        user = User.objects.filter(phone_number=phone).first()
        if user and not options["reset_password"]:
            self.stdout.write(f"ensure_admin : le compte {mask_phone(phone)} existe déjà, rien n'est modifié.")
            return

        candidate = user or User(phone_number=phone)
        try:
            validate_password(password, candidate)
        except ValidationError as exc:
            raise CommandError("ensure_admin : ADMIN_PASSWORD trop faible : " + " ".join(exc.messages)) from exc

        with transaction.atomic():
            if user is None:
                user = User.objects.create_user(phone)
                set_role(user, ROLE_SUPERUSER)
                user = User.objects.get(pk=user.pk)
                action = "créé"
            else:
                action = "mot de passe réinitialisé"
            user.set_password(password)
            user.save(update_fields=["password"])
        self.stdout.write(self.style.SUCCESS(f"ensure_admin : superutilisateur {mask_phone(phone)} {action}."))
