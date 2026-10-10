"""URLs racine d'ACCIMAP."""
from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("healthz/", views.healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),   # /login/, /verify-otp/, /logout/
    path("", include("reports.urls")),    # /report/, /anonymous-report/
    path("", include("maps.urls")),       # /map/
    path("", include("dashboard.urls")),  # /dashboard/...
    path("", include("exports.urls")),    # /dashboard/exports/
]

# Aucune route /media/ : les photos ne sont accessibles que via une vue protégée
# (dashboard:report_photo), réservée au staff autorisé.

handler403 = "config.views.permission_denied"
