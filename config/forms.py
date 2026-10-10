"""Formulaires publics hors fonctionnalités métier."""
from django import forms


class ContactForm(forms.Form):
    name = forms.CharField(
        label="Nom",
        max_length=100,
        widget=forms.TextInput(attrs={"autocomplete": "name", "class": "form-control", "placeholder": "Votre nom"}),
    )
    email = forms.EmailField(
        label="Adresse e-mail",
        max_length=254,
        widget=forms.EmailInput(
            attrs={"autocomplete": "email", "class": "form-control", "placeholder": "vous@exemple.com"}
        ),
    )
    subject = forms.CharField(
        label="Objet",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "L'objet de votre message"}),
    )
    message = forms.CharField(
        label="Message",
        min_length=10,
        max_length=5000,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 6, "placeholder": "Comment pouvons-nous vous aider ?"}
        ),
    )
    website = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"}
        ),
    )

    def clean_subject(self):
        subject = self.cleaned_data["subject"]
        if "\r" in subject or "\n" in subject:
            raise forms.ValidationError("L'objet ne peut pas contenir de saut de ligne.")
        return subject
