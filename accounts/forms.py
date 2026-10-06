from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from .phone import normalize_phone


class PhoneForm(forms.Form):
    phone_number = forms.CharField(
        label="Numéro de téléphone",
        max_length=25,
        help_text="Numéro togolais à 8 chiffres, ou format international (+…).",
        widget=forms.TextInput(
            attrs={
                "type": "tel",
                "inputmode": "tel",
                "autocomplete": "tel",
                "placeholder": "Ex. 90 12 34 56",
                "class": "form-control form-control-lg",
                "autofocus": True,
            }
        ),
    )

    def clean_phone_number(self):
        return normalize_phone(self.cleaned_data["phone_number"])


class OTPForm(forms.Form):
    code = forms.CharField(
        label="Code de vérification",
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "pattern": "[0-9]*",
                "class": "form-control form-control-lg otp-input",
                "placeholder": "••••••",
                "autofocus": True,
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs["maxlength"] = settings.OTP_LENGTH
        self.fields["code"].widget.attrs["placeholder"] = "•" * settings.OTP_LENGTH

    def clean_code(self):
        code = self.cleaned_data["code"].replace(" ", "")
        if not code.isdigit() or len(code) != settings.OTP_LENGTH:
            raise ValidationError(f"Le code comporte {settings.OTP_LENGTH} chiffres.")
        return code
