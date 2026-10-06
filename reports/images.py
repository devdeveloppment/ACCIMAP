"""
Nettoyage des photos AVANT stockage.

Une photo prise au smartphone contient des métadonnées (EXIF) : position GPS exacte,
modèle d'appareil, date, parfois le nom du propriétaire. Pour ne jamais exposer
l'identité ou la position du déclarant (surtout en mode anonyme), chaque photo est
entièrement ré-encodée à partir de ses seuls pixels :

* l'orientation EXIF est d'abord appliquée (la photo reste à l'endroit) ;
* toutes les métadonnées (EXIF, GPS, XMP, profils ICC, commentaires, vignettes) sont
  abandonnées : seul le contenu visuel est réécrit ;
* l'image est réduite si son plus grand côté dépasse PHOTO_MAX_DIMENSION.
"""
import io

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps

_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


def _normalize_mode(image):
    """Ramène l'image à un mode simple, en conservant la transparence éventuelle."""
    if image.mode in ("RGB", "RGBA", "L", "LA"):
        return image
    if image.mode == "P":
        return image.convert("RGBA" if "transparency" in image.info else "RGB")
    return image.convert("RGB")  # CMYK, 16 bits, etc.


def sanitize_photo(uploaded_file, max_dimension=None):
    """
    Retourne un nouveau fichier (InMemoryUploadedFile) sans aucune métadonnée.
    Lève ValidationError si le fichier n'est pas une image exploitable.
    """
    max_dimension = max_dimension or settings.PHOTO_MAX_DIMENSION
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as source:
            image_format = source.format
            source.load()
            image = ImageOps.exif_transpose(source)  # copie, orientation appliquée
    except Exception as exc:
        raise ValidationError("Le fichier n'est pas une image valide.") from exc

    if image_format not in _FORMATS:
        raise ValidationError("Le fichier n'est pas une image JPEG, PNG ou WebP valide.")

    image = _normalize_mode(image)
    if image_format == "JPEG" and image.mode in ("RGBA", "LA"):
        image = image.convert("RGB")
    image.info = {}  # abandonne toute métadonnée résiduelle

    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    if image_format == "JPEG":
        image.save(buffer, "JPEG", quality=85, optimize=True)
    elif image_format == "PNG":
        image.save(buffer, "PNG", optimize=True)
    else:
        image.save(buffer, "WEBP", quality=85)

    extension, content_type = _FORMATS[image_format]
    size = buffer.tell()
    buffer.seek(0)
    # Nom neutre : le nom d'origine (potentiellement personnel) est abandonné.
    return InMemoryUploadedFile(buffer, "photo", f"photo.{extension}", content_type, size, None)
