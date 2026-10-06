"""
Niveaux d'accès d'ACCIMAP, fondés sur les permissions Django.

* Utilisateur      : citoyen, aucun accès à l'administration.
* Staff            : traite les signalements. Accès au tableau de bord si la permission
                     « voir » est accordée ; « modifier » et « supprimer » sont des permissions
                     distinctes, ajustables par un superutilisateur.
* Superutilisateur : tous les droits du staff + gestion des utilisateurs et des permissions.

Ces permissions sont aussi respectées par le Django Admin (/admin/).
"""
from django.contrib.auth.models import Permission
from django.db import transaction

ROLE_USER = "user"
ROLE_STAFF = "staff"
ROLE_SUPERUSER = "superuser"
ROLE_CHOICES = [
    (ROLE_USER, "Utilisateur (aucun accès administrateur)"),
    (ROLE_STAFF, "Staff (traitement des signalements)"),
    (ROLE_SUPERUSER, "Superutilisateur (tous les droits, gestion des utilisateurs)"),
]
ROLE_LABELS = {
    ROLE_USER: "Utilisateur",
    ROLE_STAFF: "Staff",
    ROLE_SUPERUSER: "Superutilisateur",
}

PERM_VIEW = "reports.view_accidentreport"
PERM_CHANGE = "reports.change_accidentreport"
PERM_DELETE = "reports.delete_accidentreport"


def get_role(user):
    if user.is_superuser:
        return ROLE_SUPERUSER
    if user.is_staff:
        return ROLE_STAFF
    return ROLE_USER


def _report_permission(app_perm):
    app_label, codename = app_perm.split(".")
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


@transaction.atomic
def set_role(user, role, can_change=True, can_delete=True):
    """
    Définit entièrement le niveau d'accès d'un utilisateur (drapeaux + permissions).

    Pour un staff, la permission « voir » est toujours accordée (sans elle, le tableau de bord
    serait inutilisable) ; « modifier » et « supprimer » dépendent des arguments.
    """
    if role not in ROLE_LABELS:
        raise ValueError(f"Rôle inconnu : {role!r}")

    user.is_staff = role in (ROLE_STAFF, ROLE_SUPERUSER)
    user.is_superuser = role == ROLE_SUPERUSER
    fields = ["is_staff", "is_superuser"]
    if role == ROLE_USER and user.has_usable_password():
        # Un citoyen se connecte par OTP uniquement : on retire tout mot de passe d'administration.
        user.set_unusable_password()
        fields.append("password")
    user.save(update_fields=fields)

    user.user_permissions.clear()
    user.groups.clear()
    if role == ROLE_STAFF:
        granted = [PERM_VIEW]
        if can_change:
            granted.append(PERM_CHANGE)
        if can_delete:
            granted.append(PERM_DELETE)
        user.user_permissions.add(*[_report_permission(p) for p in granted])
    return user


def get_report_permissions(user):
    """
    Droits « signalements » accordés à un compte, lus dans la base.

    N'utilise PAS user.has_perm() : Django répond toujours False pour un compte désactivé, ce qui
    ferait croire qu'un staff désactivé n'a aucun droit (et les effacerait à la réactivation).
    """
    if user.is_superuser:
        return {"view": True, "change": True, "delete": True}
    granted = set(
        user.user_permissions.filter(
            content_type__app_label="reports", content_type__model="accidentreport"
        ).values_list("codename", flat=True)
    )
    return {
        "view": "view_accidentreport" in granted,
        "change": "change_accidentreport" in granted,
        "delete": "delete_accidentreport" in granted,
    }
