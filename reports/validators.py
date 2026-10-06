"""
Validation SERVEUR des photos de signalement.

Ces contrôles s'ajoutent à ceux du navigateur et ne s'y fient jamais :
extension, taille, et contenu réel du fichier (c'est bien une image).
"""
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image

ALLOWED_PILLOW_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 25_000_000  # protège contre les images « bombes de décompression »


def _is_already_stored(file):
    # Un fichier déjà enregistré n'est pas re-validé (ex. édition dans l'admin).
    return getattr(file, "_committed", False)


def validate_photo_extension(file):
    if _is_already_stored(file):
        return
    extension = os.path.splitext(file.name)[1].lower().lstrip(".")
    if extension not in settings.ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(settings.ALLOWED_IMAGE_EXTENSIONS)
        raise ValidationError(f"Format non autorisé. Formats acceptés : {allowed}.")


def validate_photo_size(file):
    if _is_already_stored(file):
        return
    if file.size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise ValidationError(
            f"La photo est trop volumineuse (maximum {settings.MAX_UPLOAD_SIZE_MB} Mo)."
        )


def validate_photo_content(file):
    if _is_already_stored(file):
        return
    try:
        file.seek(0)
        with Image.open(file) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except Exception:  # fichier corrompu, faux .jpg, bombe de décompression...
        raise ValidationError("Le fichier n'est pas une image valide.")
    finally:
        file.seek(0)

    if image_format not in ALLOWED_PILLOW_FORMATS:
        raise ValidationError("Le fichier n'est pas une image JPEG, PNG ou WebP valide.")
    if width * height > MAX_PIXELS:
        raise ValidationError("Les dimensions de l'image sont trop grandes.")
