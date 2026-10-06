"""
Contrôle d'accès à l'espace d'administration PRIVÉ.

Deux authentifications distinctes coexistent :
  * citoyenne : numéro + code OTP (accounts/views.py) -> interface publique uniquement ;
  * administrateur : identifiant + mot de passe (dashboard/auth.py) -> espace privé.

Une session ne vaut « administrateur » que si elle a été ouverte par la connexion de l'espace privé : elle porte alors
la marque ADMIN_SESSION_KEY, liée à l'identifiant de l'utilisateur. Un compte staff connecté par OTP n'y a donc PAS accès.

Règles appliquées côté serveur à chaque requête :
  * citoyen connecté (compte non staff)                 -> 403, jamais de redirection vers l'administration ;
  * visiteur, ou staff sans session administrateur      -> page de connexion de l'espace privé (401 pour les API) ;
  * session administrateur sans la permission demandée  -> 403.
"""
from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache

ADMIN_SESSION_KEY = "accimap_admin_uid"


def is_admin_session(request):
    """Vrai si la session a été ouverte par la connexion administrateur, pour CET utilisateur, toujours staff."""
    user = request.user
    return bool(
        user.is_authenticated and user.is_staff and request.session.get(ADMIN_SESSION_KEY) == user.pk
    )


def mark_admin_session(request, user):
    request.session[ADMIN_SESSION_KEY] = user.pk
    request.session.set_expiry(settings.ADMIN_SESSION_AGE)


def admin_login_redirect(request):
    return redirect_to_login(request.get_full_path(), login_url=reverse("dashboard:login"))


def _check_admin(request, perms, api, superuser=False):
    """Retourne une réponse de refus, ou None si l'accès est accordé."""
    user = request.user
    if user.is_authenticated and not user.is_staff:
        if api:
            return JsonResponse({"error": "Accès refusé."}, status=403)
        raise PermissionDenied                     # citoyen : refus net
    if not is_admin_session(request):
        if api:
            return JsonResponse({"error": "Authentification administrateur requise."}, status=401)
        return admin_login_redirect(request)
    if (superuser and not user.is_superuser) or not user.has_perms(perms):
        if api:
            return JsonResponse({"error": "Accès refusé."}, status=403)
        raise PermissionDenied
    return None


def staff_permission_required(*perms, api=False):
    """Espace privé : session administrateur + TOUTES les permissions données. Réponses jamais mises en cache."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            refusal = _check_admin(request, perms, api)
            return refusal or view_func(request, *args, **kwargs)

        return never_cache(wrapper)

    return decorator


def staff_required(view_func):
    """Session administrateur, sans permission particulière."""
    return staff_permission_required()(view_func)


def superuser_required(view_func):
    """Session administrateur d'un superutilisateur (gestion des utilisateurs et des permissions)."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        refusal = _check_admin(request, (), api=False, superuser=True)
        return refusal or view_func(request, *args, **kwargs)

    return never_cache(wrapper)
