"""Django Admin (secours) : même règle que l'espace privé et même page de connexion."""
from django.contrib import admin
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import reverse

from accounts.decorators import is_admin_session


class AccimapAdminSite(admin.AdminSite):
    site_header = "Administration ACCIMAP (secours)"
    site_title = "ACCIMAP : administration"
    index_title = "Administration de secours"

    def has_permission(self, request):
        # Un compte staff connecté par OTP citoyen n'a PAS accès : il faut une session administrateur.
        return super().has_permission(request) and is_admin_session(request)

    def login(self, request, extra_context=None):
        """Un seul point d'entrée : la connexion de l'espace d'administration (identifiant + mot de passe)."""
        return redirect_to_login(request.GET.get("next") or reverse("admin:index"), login_url=reverse("dashboard:login"))

    def logout(self, request, extra_context=None):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        auth_logout(request)
        return redirect("dashboard:login")
