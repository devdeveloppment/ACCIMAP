"""Comptes administrateurs et utilisateurs associés aux actions de gestion."""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from .phone import normalize_phone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone_number, password=None, **extra_fields):
        """Crée un compte utilisateur ; seul le personnel autorisé peut se connecter."""
        phone_number = normalize_phone(phone_number)
        user = self.model(phone_number=phone_number, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, password=None, **extra_fields):
        """Administrateur : le mot de passe est obligatoire (connexion à /admin/)."""
        if not password:
            raise ValueError("Un administrateur doit avoir un mot de passe.")
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(phone_number, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Compte ACCIMAP utilisé pour protéger et tracer les actions d'administration.

    Les signalements publics sont anonymes et ne nécessitent pas de compte.
    Le numéro n'est JAMAIS affiché publiquement.
    """

    phone_number = models.CharField("numéro de téléphone", max_length=20, unique=True)
    is_active = models.BooleanField("actif", default=True)
    is_staff = models.BooleanField("administrateur", default=False)
    date_joined = models.DateTimeField("date d'inscription", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"
        ordering = ["-date_joined"]

    def __str__(self):
        return self.phone_number

    def clean(self):
        super().clean()
        self.phone_number = normalize_phone(self.phone_number)
