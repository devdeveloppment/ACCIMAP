"""Configuration de l'application Django Admin, avec le site d'administration d'ACCIMAP."""
from django.contrib.admin.apps import AdminConfig


class AccimapAdminConfig(AdminConfig):
    """Remplace « django.contrib.admin » dans INSTALLED_APPS : même application, site AccimapAdminSite."""

    default_site = "dashboard.admin_site.AccimapAdminSite"
