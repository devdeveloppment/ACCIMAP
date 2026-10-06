"""
Authentification de l'espace d'administration PRIVÉ : identifiant administrateur + mot de passe.

  * L'identifiant est le numéro du compte (champ identifiant de Django), accepté sous tout format.
  * Seuls les comptes staff actifs disposant d'un mot de passe peuvent se connecter ; un citoyen (OTP uniquement)
    n'a pas de mot de passe et ne peut donc jamais ouvrir cet espace.
  * Message d'erreur unique, quel que soit le motif (pas de divulgation de l'existence d'un compte).
  * Blocage temporaire après ADMIN_LOGIN_MAX_ATTEMPTS échecs pour un même identifiant.
"""
import hashlib
import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from accounts.decorators import is_admin_session, mark_admin_session, staff_required
from accounts.phone import mask_phone, normalize_phone

logger = logging.getLogger(__name__)
GENERIC_ERROR = "Identifiant ou mot de passe incorrect, ou compte sans accès à l'administration."
LOCKED_ERROR = "Trop de tentatives de connexion. Réessayez dans quelques minutes."


class AdminLoginForm(AuthenticationForm):
    error_messages = {"invalid_login": GENERIC_ERROR, "inactive": GENERIC_ERROR}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Identifiant administrateur"
        self.fields["username"].help_text = "Numéro de téléphone du compte administrateur."
        self.fields["username"].widget.attrs.update(
            {"class": "form-control form-control-lg", "autocomplete": "username", "inputmode": "tel", "autofocus": True}
        )
        self.fields["password"].label = "Mot de passe"
        self.fields["password"].widget.attrs.update({"class": "form-control form-control-lg", "autocomplete": "current-password"})

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_staff:
            raise ValidationError(GENERIC_ERROR, code="invalid_login")


def _throttle_key(identifier):
    try:
        identifier = normalize_phone(identifier)
    except ValidationError:
        identifier = (identifier or "").strip()[:40]
    return "admin-login-failures:" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def _safe_next(request):
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return ""


@sensitive_post_parameters("password")
@csrf_protect
@never_cache
def admin_login(request):
    next_url = _safe_next(request)
    if is_admin_session(request):
        return redirect(next_url or "dashboard:index")

    form = AdminLoginForm(request, data=request.POST or None)
    locked_error = None
    if request.method == "POST":
        key = _throttle_key(request.POST.get("username", ""))
        if cache.get(key, 0) >= settings.ADMIN_LOGIN_MAX_ATTEMPTS:
            # Formulaire vierge (aucune authentification tentée) et message transmis à part.
            form = AdminLoginForm(request)
            locked_error = LOCKED_ERROR
            logger.warning("Connexion administrateur bloquée temporairement (trop d'échecs).")
        elif form.is_valid():
            user = form.get_user()
            auth_login(request, user)
            mark_admin_session(request, user)
            cache.delete(key)
            logger.info("Connexion à l'espace d'administration : %s", mask_phone(user.phone_number))
            return redirect(next_url or "dashboard:index")
        else:
            cache.add(key, 0, settings.ADMIN_LOGIN_LOCK_SECONDS)
            cache.incr(key)
            logger.info("Échec de connexion à l'espace d'administration.")
    return render(request, "dashboard/login.html", {"form": form, "next": next_url, "locked_error": locked_error})


@require_POST
def admin_logout(request):
    auth_logout(request)
    messages.success(request, "Vous êtes déconnecté de l'espace d'administration.")
    return redirect("dashboard:login")


def _style(form):
    for field in form.fields.values():
        field.widget.attrs["class"] = "form-control"
    return form


@sensitive_post_parameters()
@staff_required
def password_change(request):
    form = _style(PasswordChangeForm(request.user, request.POST or None))
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)          # garde la session (et la marque administrateur)
        messages.success(request, "Votre mot de passe a été modifié.")
        return redirect("dashboard:index")
    return render(request, "dashboard/password_change.html", {"form": form})
