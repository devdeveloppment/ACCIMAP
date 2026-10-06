"""Normalisation et validation des numéros de téléphone (format E.164)."""
import re

from django.conf import settings
from django.core.exceptions import ValidationError

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
TOGO_PREFIX = "+228"
TOGO_LENGTH = len(TOGO_PREFIX) + 8  # +228 suivi de 8 chiffres


def normalize_phone(raw):
    """
    Retourne le numéro au format international « +22890123456 ».

    Accepte : "90 12 34 56", "+228 90 12 34 56", "00228 90123456", "22890123456".
    Un numéro sans préfixe reçoit l'indicatif par défaut (settings.DEFAULT_PHONE_COUNTRY_CODE).
    Lève ValidationError si le numéro est invalide.
    """
    value = re.sub(r"[\s\-.()]", "", str(raw or ""))

    if value.startswith("00"):
        value = "+" + value[2:]
    elif not value.startswith("+"):
        if not value.isdigit():
            raise ValidationError("Numéro de téléphone invalide.", code="invalid_phone")
        default = settings.DEFAULT_PHONE_COUNTRY_CODE  # ex. "+228"
        default_digits = default.lstrip("+")
        # « 22890123456 » : l'indicatif est déjà présent, il manque juste le « + ».
        if value.startswith(default_digits) and len(value) >= len(default_digits) + 8:
            value = "+" + value
        else:
            value = default + value

    if not E164_RE.match(value):
        raise ValidationError("Numéro de téléphone invalide.", code="invalid_phone")
    # Règle propre au Togo : 8 chiffres après l'indicatif.
    if value.startswith(TOGO_PREFIX) and len(value) != TOGO_LENGTH:
        raise ValidationError(
            "Un numéro togolais comporte 8 chiffres (ex. 90 12 34 56).", code="invalid_phone"
        )
    return value


def mask_phone(phone):
    """« +22890123456 » -> « +228****56 » (pour les logs et l'affichage restreint)."""
    phone = str(phone or "")
    if len(phone) <= 6:
        return "****"
    return f"{phone[:4]}****{phone[-2:]}"
