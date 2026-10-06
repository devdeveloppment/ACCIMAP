"""
Connexion par téléphone : numéro -> envoi OTP -> saisie du code -> session.

Entre les deux étapes, le numéro (déjà normalisé) est conservé dans la session,
jamais dans l'URL. Le code OTP en clair n'est mémorisé dans la session QUE si
OTP_DEV_MODE est actif (bandeau « Mode développement — SMS simulé »).
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import FormView

from .forms import OTPForm, PhoneForm
from .models import OTPCode
from .otp import OTPError, OTPResendTooSoon, request_otp, verify_otp
from .phone import mask_phone

SESSION_PHONE = "otp_phone"
SESSION_NEXT = "otp_next"
SESSION_DEV_CODE = "otp_dev_code"
BACKEND = "accounts.backends.PhoneModelBackend"


def _safe_next(request, value):
    """N'accepte qu'une redirection interne (protection contre l'open redirect)."""
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return ""


def _clear_otp_session(request):
    for key in (SESSION_PHONE, SESSION_NEXT, SESSION_DEV_CODE):
        request.session.pop(key, None)


def _send_code(request, phone):
    """
    Demande l'envoi d'un code. Retourne None si tout va bien (ou si un code
    vient déjà d'être envoyé), sinon le message d'erreur à afficher.
    """
    try:
        result = request_otp(phone)
    except OTPResendTooSoon as exc:
        messages.info(request, f"Un code a déjà été envoyé à ce numéro. {exc}")
        return None
    except OTPError as exc:
        return str(exc)

    # dev_code n'est renseigné que si OTP_DEV_MODE=True.
    if result.dev_code:
        request.session[SESSION_DEV_CODE] = result.dev_code
    else:
        request.session.pop(SESSION_DEV_CODE, None)
    if not settings.OTP_DEV_MODE:
        messages.success(request, "Un code de vérification vous a été envoyé par SMS.")
    return None


class LoginView(FormView):
    """Étape 1 : saisie du numéro de téléphone."""

    template_name = "accounts/login.html"
    form_class = PhoneForm
    success_url = reverse_lazy("accounts:verify_otp")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            messages.info(request, "Vous êtes déjà connecté.")
            return redirect(settings.LOGIN_REDIRECT_URL)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next"] = self.request.POST.get("next") or self.request.GET.get("next", "")
        return context

    def form_valid(self, form):
        phone = form.cleaned_data["phone_number"]
        if self.request.session.get(SESSION_PHONE) != phone:
            self.request.session.pop(SESSION_DEV_CODE, None)  # code d'un autre numéro

        error = _send_code(self.request, phone)
        if error:
            form.add_error(None, error)
            return self.form_invalid(form)

        self.request.session[SESSION_PHONE] = phone
        self.request.session[SESSION_NEXT] = _safe_next(self.request, self.request.POST.get("next"))
        return super().form_valid(form)


class VerifyOTPView(FormView):
    """Étape 2 : saisie du code reçu."""

    template_name = "accounts/verify_otp.html"
    form_class = OTPForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(settings.LOGIN_REDIRECT_URL)
        if not request.session.get(SESSION_PHONE):
            messages.warning(request, "Veuillez d'abord saisir votre numéro de téléphone.")
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)

    def _resend_remaining(self, phone):
        """Secondes restantes avant de pouvoir demander un nouveau code."""
        last = OTPCode.objects.filter(phone_number=phone).first()
        if not last:
            return 0
        elapsed = (timezone.now() - last.created_at).total_seconds()
        return max(0, int(settings.OTP_RESEND_DELAY_SECONDS - elapsed) + 1)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        phone = self.request.session[SESSION_PHONE]
        context.update(
            masked_phone=mask_phone(phone),
            expiry_minutes=settings.OTP_EXPIRY_MINUTES,
            resend_remaining=self._resend_remaining(phone),
            # Double garde : le code n'est exposé que si le mode test est actif.
            dev_mode=settings.OTP_DEV_MODE,
            dev_code=self.request.session.get(SESSION_DEV_CODE) if settings.OTP_DEV_MODE else None,
        )
        return context

    def form_valid(self, form):
        phone = self.request.session[SESSION_PHONE]
        try:
            user = verify_otp(phone, form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        next_url = self.request.session.get(SESSION_NEXT) or settings.LOGIN_REDIRECT_URL
        login(self.request, user, backend=BACKEND)  # change l'identifiant de session
        _clear_otp_session(self.request)
        messages.success(self.request, "Connexion réussie. Bienvenue sur ACCIMAP !")
        return redirect(next_url)


class ResendOTPView(View):
    """Renvoi d'un nouveau code (POST uniquement)."""

    http_method_names = ["post"]

    def post(self, request):
        phone = request.session.get(SESSION_PHONE)
        if not phone:
            return redirect("accounts:login")
        error = _send_code(request, phone)
        if error:
            messages.error(request, error)
        return redirect("accounts:verify_otp")


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "Vous avez été déconnecté.")
    return redirect(settings.LOGOUT_REDIRECT_URL)
