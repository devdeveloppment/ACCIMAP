"""Outils partagés par les tests de reports (et réutilisables par les autres apps)."""
import io
from datetime import timedelta

from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from reports.models import AccidentReport

LOME = (1.2314, 6.1725)  # (longitude, latitude)


def make_report(**overrides):
    """Crée un signalement valide ; chaque champ peut être surchargé."""
    data = {
        "accident_type": "COLLISION",
        "accident_date": timezone.localdate() - timedelta(days=1),
        "accident_time": "14:30",
        "severity": "MEDIUM",
        "location": Point(*LOME, srid=4326),
    }
    data.update(overrides)
    return AccidentReport.objects.create(**data)


def make_image(name="photo.png", image_format="PNG", size=(20, 20)):
    """Fabrique un vrai fichier image en mémoire."""
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{image_format.lower()}")


def valid_report_data(**overrides):
    """Données POST valides pour le formulaire de signalement (position à Lomé)."""
    data = {
        "accident_type": "RUN_OFF_ROAD",
        "accident_date": (timezone.localdate() - timedelta(days=1)).isoformat(),
        "accident_time": "18:45",
        "severity": "SEVERE",
        "vehicle_count": "1",
        "injured_count": "2",
        "death_count": "0",
        "description": "Moto contre un taxi au carrefour.",
        "latitude": "6.131900",
        "longitude": "1.222800",
    }
    data.update(overrides)
    return data


def make_jpeg_with_exif(size=(400, 200)):
    """JPEG contenant des métadonnées personnelles : GPS, marque, modèle, auteur, orientation."""
    image = Image.new("RGB", size, (30, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 15 Pro"
    exif[0x0112] = 6
    exif[0x013B] = "Jean Dupont"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (6.0, 7.0, 54.0), "E", (1.0, 13.0, 22.0)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()
