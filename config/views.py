"""Vues techniques du projet (hors fonctionnalités métier)."""
import hashlib
import logging
import smtplib

from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import ContactForm

logger = logging.getLogger(__name__)


def healthz(request):
    """Sonde de santé utilisée par Render : vérifie que la base répond."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


def home(request):
    """Page d'accueil publique. Le seul chiffre affiché est public : signalements vérifiés."""
    from reports.models import AccidentReport

    return render(request, "home.html", {"verified_count": AccidentReport.objects.public().count()})


def about(request):
    """Présentation du projet."""
    return render(request, "about.html")


def contact(request):
    """Formulaire public, protégé contre les soumissions automatisées."""
    form = ContactForm(request.POST or None)
    context = {"form": form, "contact_email": settings.CONTACT_RECEIVER_EMAIL}
    if request.method == "POST":
        if not form.is_valid():
            return render(request, "contact.html", context, status=400)

        if form.cleaned_data["website"]:
            form.add_error(None, "Le formulaire n'a pas pu être envoyé. Vérifiez les champs et réessayez.")
            return render(request, "contact.html", context, status=400)

        remote_addr = request.META.get("REMOTE_ADDR", "unknown")
        address_hash = hashlib.sha256(remote_addr.encode("utf-8")).hexdigest()[:24]
        rate_key = f"contact-rate:{address_hash}"
        cache.add(rate_key, 0, settings.CONTACT_RATE_WINDOW_SECONDS)
        if cache.incr(rate_key) > settings.CONTACT_RATE_LIMIT:
            form.add_error(None, "Trop de messages ont été envoyés. Réessayez dans un peu plus tard.")
            return render(request, "contact.html", context, status=429)

        if not all(
            (settings.EMAIL_HOST, settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD, settings.DEFAULT_FROM_EMAIL)
        ):
            logger.error("Configuration SMTP incomplète pour le formulaire de contact.")
            form.add_error(None, "L'envoi est temporairement indisponible. Vous pouvez nous écrire directement par e-mail.")
            return render(request, "contact.html", context, status=503)

        try:
            email = EmailMessage(
                subject=f"[ACCIMAP] {form.cleaned_data['subject']}",
                body=(
                    f"Nom : {form.cleaned_data['name']}\n"
                    f"E-mail : {form.cleaned_data['email']}\n\n"
                    f"{form.cleaned_data['message']}"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[settings.CONTACT_RECEIVER_EMAIL],
                reply_to=[form.cleaned_data["email"]],
            )
            sent_count = email.send(fail_silently=False)
        except (OSError, smtplib.SMTPException, ImproperlyConfigured) as exc:
            logger.error("Échec d'envoi SMTP du formulaire de contact (%s).", type(exc).__name__)
            form.add_error(None, "Votre message n'a pas pu être envoyé. Réessayez plus tard ou contactez-nous directement.")
            return render(request, "contact.html", context, status=503)

        if sent_count != 1:
            logger.error("Le backend SMTP n'a confirmé aucun message pour le formulaire de contact.")
            form.add_error(None, "Votre message n'a pas pu être envoyé. Réessayez plus tard ou contactez-nous directement.")
            return render(request, "contact.html", context, status=503)

        messages.success(request, "Votre message a bien été envoyé. Nous vous répondrons dès que possible.")
        return redirect(reverse("contact"))

    return render(request, "contact.html", context)


def permission_denied(request, exception=None):
    """Page 403 personnalisée (accès réservé aux administrateurs)."""
    return render(request, "403.html", status=403)
