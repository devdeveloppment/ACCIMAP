"""Backend d'authentification : accepte un numéro saisi sous n'importe quel format."""
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError

from .phone import normalize_phone


class PhoneModelBackend(ModelBackend):
    """
    Utilisé pour la connexion par mot de passe des administrateurs.
    Normalise le numéro (« 90123456 » -> « +22890123456 ») avant de comparer.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get("phone_number")
        try:
            username = normalize_phone(username)
        except ValidationError:
            return None
        return super().authenticate(request, username=username, password=password, **kwargs)
