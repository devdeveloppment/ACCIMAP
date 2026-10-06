from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    # Signalement identifié : connexion (OTP) obligatoire.
    path("report/", login_required(views.ReportCreateView.as_view(anonymous=False)), name="create"),
    # Signalement anonyme : ouvert à tous, aucun compte rattaché.
    path(
        "anonymous-report/",
        views.ReportCreateView.as_view(anonymous=True),
        name="anonymous_create",
    ),
    path("report/success/", views.report_success, name="success"),
    path("report/check-position/", views.check_position, name="check_position"),
    path("report/place/", views.place_lookup, name="place"),
]
