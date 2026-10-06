"""Traçabilité des actions d'administration (journal d'historique de Django, LogEntry)."""
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType

__all__ = ["ADDITION", "CHANGE", "DELETION", "log_action", "log_export", "EXPORT_TAG"]

EXPORT_TAG = "[EXPORT]"


def log_action(actor, obj, flag, message, repr_text=None):
    """
    Enregistre qui a fait quoi. Visible dans le Django Admin (superutilisateur).
    `repr_text` permet de ne jamais écrire de numéro de téléphone complet dans le journal.
    """
    LogEntry.objects.create(
        user_id=actor.pk,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=str(obj.pk),
        object_repr=(repr_text or str(obj))[:200],
        action_flag=flag,
        change_message=message,
    )


def log_export(actor, fmt, count, filters_text):
    """Un export de données doit laisser une trace : qui, quel format, combien de lignes, quels filtres."""
    from reports.models import AccidentReport

    LogEntry.objects.create(
        user_id=actor.pk,
        content_type=ContentType.objects.get_for_model(AccidentReport),
        object_id=None,
        object_repr=f"Export {fmt} ({count} signalement(s))",
        action_flag=CHANGE,
        change_message=f"{EXPORT_TAG} {fmt} : {count} signalement(s). Filtres : {filters_text}.",
    )
