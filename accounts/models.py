"""Utilisateurs (authentification par téléphone) et codes OTP."""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from .phone import normalize_phone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone_number, password=None, **extra_fields):
        """
        Crée un utilisateur « normal » (citoyen). Sans mot de passe : il se
        connecte uniquement par OTP, son mot de passe est donc inutilisable.
        """
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
    Utilisateur ACCIMAP, identifié uniquement par son numéro de téléphone.

    - Citoyen : is_staff=False, connexion par OTP.
    - Administrateur : is_staff=True (+ permissions Django), accès à /dashboard/.
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


class OTPCode(models.Model):
    """
    Code à usage unique envoyé par SMS.

    Le code en clair n'est jamais stocké : seulement son empreinte (HMAC).
    On référence le numéro (et non un User) car l'utilisateur est créé
    seulement après la première vérification réussie.
    """

    phone_number = models.CharField("numéro de téléphone", max_length=20, db_index=True)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField("tentatives", default=0)
    is_used = models.BooleanField("utilisé / invalidé", default=False)

    class Meta:
        verbose_name = "code OTP"
        verbose_name_plural = "codes OTP"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["phone_number", "-created_at"])]

    def __str__(self):
        return f"OTP {self.phone_number} ({self.created_at:%d/%m/%Y %H:%M})"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at
