from django import forms

from accounts.roles import ROLE_CHOICES, ROLE_STAFF, ROLE_SUPERUSER, get_report_permissions, get_role
from reports.choices import ReportStatus
from reports.forms import ReportDetailsForm


class ReportReviewForm(forms.Form):
    """Changement de statut et note de l'administrateur."""

    status = forms.ChoiceField(choices=ReportStatus.choices)
    admin_note = forms.CharField(
        required=False,
        max_length=2000,
        label="Note de l'administrateur",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control", "maxlength": 2000}),
    )


class ReportEditForm(ReportDetailsForm):
    """Modification d'un signalement par un administrateur.

    Seuls les détails de l'accident sont modifiables. Le mode anonyme, le déclarant, le statut et la
    position ne peuvent PAS être modifiés ici (ils ne figurent pas dans le formulaire)."""

    remove_photo = forms.BooleanField(
        required=False, label="Supprimer les photos (par ex. si elles montrent des personnes identifiables)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not (self.instance and self.instance.photo):
            del self.fields["remove_photo"]
        else:
            self.fields["remove_photo"].widget.attrs["class"] = "form-check-input"


class UserAccessForm(forms.Form):
    """Rôle, permissions et activation d'un compte (superutilisateur uniquement)."""

    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect, label="Niveau d'accès")
    is_active = forms.BooleanField(required=False, label="Compte actif")
    can_change = forms.BooleanField(
        required=False, label="Peut modifier les signalements et changer leur statut"
    )
    can_delete = forms.BooleanField(required=False, label="Peut supprimer des signalements")

    def __init__(self, *args, target, actor, **kwargs):
        self.target, self.actor = target, actor
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            rights = get_report_permissions(target)   # fiable même si le compte est désactivé
            self.initial = {
                "role": get_role(target),
                "is_active": target.is_active,
                "can_change": rights["change"],
                "can_delete": rights["delete"],
            }
        for name in ("is_active", "can_change", "can_delete"):
            self.fields[name].widget.attrs["class"] = "form-check-input"

    def clean(self):
        cleaned = super().clean()
        if self.target.pk == self.actor.pk:
            # Garde-fou : un superutilisateur ne peut ni se désactiver ni se rétrograder
            # (sinon risque de verrouiller l'administration).
            if not cleaned.get("is_active") or cleaned.get("role") != ROLE_SUPERUSER:
                raise forms.ValidationError(
                    "Vous ne pouvez ni désactiver votre propre compte ni réduire vos propres droits."
                )
        if cleaned.get("role") != ROLE_STAFF:
            cleaned["can_change"] = cleaned["can_delete"] = False
        return cleaned
