from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    # Tous les signalements publics sont accessibles sans connexion et restent anonymes.
    path("report/", views.ReportCreateView.as_view(anonymous=True), name="create"),
    # Ancienne URL conservée pour les liens déjà partagés.
    path("anonymous-report/", views.ReportCreateView.as_view(anonymous=True), name="anonymous_create"),
    path("report/success/", views.report_success, name="success"),
    path("report/check-position/", views.check_position, name="check_position"),
    path("report/place/", views.place_lookup, name="place"),
]
