"""Nettoyage des fichiers photo lorsqu'un signalement (ou une photo) est supprimé."""
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import AccidentReport, ReportPhoto


@receiver(post_delete, sender=AccidentReport)
def delete_photo_file(sender, instance, **kwargs):
    """Supprime la photo de couverture du stockage une fois la suppression validée en base."""
    if instance.photo:
        storage, name = instance.photo.storage, instance.photo.name
        transaction.on_commit(lambda: storage.delete(name))


@receiver(post_delete, sender=ReportPhoto)
def delete_extra_photo_file(sender, instance, **kwargs):
    """Supprime le fichier d'une photo supplémentaire (y compris lors d'un CASCADE)."""
    if instance.image:
        storage, name = instance.image.storage, instance.image.name
        transaction.on_commit(lambda: storage.delete(name))
