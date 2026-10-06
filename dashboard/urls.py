from django.urls import path

from . import auth, users, views

app_name = "dashboard"

urlpatterns = [
    # Authentification PROPRE à l'espace privé (identifiant + mot de passe)
    path("dashboard/login/", auth.admin_login, name="login"),
    path("dashboard/logout/", auth.admin_logout, name="logout"),
    path("dashboard/password/", auth.password_change, name="password_change"),
    path("dashboard/", views.index, name="index"),
    path("dashboard/statistics/", views.statistics, name="statistics"),
    path("dashboard/reports/", views.report_list, name="report_list"),
    path("dashboard/reports/<uuid:pk>/", views.report_detail, name="report_detail"),
    path("dashboard/reports/<uuid:pk>/photo/", views.report_photo, name="report_photo"),
    path("dashboard/reports/<uuid:pk>/review/", views.report_review, name="report_review"),
    path("dashboard/reports/<uuid:pk>/edit/", views.report_edit, name="report_edit"),
    path("dashboard/reports/<uuid:pk>/delete/", views.report_delete, name="report_delete"),
    path("dashboard/map/", views.admin_map, name="map"),
    path("dashboard/map/data/", views.admin_map_data, name="map_data"),
    path("dashboard/users/", users.user_list, name="user_list"),
    path("dashboard/users/<int:pk>/", users.user_detail, name="user_detail"),
    path("dashboard/users/<int:pk>/password/", users.user_set_password, name="user_set_password"),
]
