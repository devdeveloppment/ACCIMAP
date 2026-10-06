from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path("dashboard/exports/", views.index, name="index"),
    path("dashboard/exports/csv/", views.export_csv, name="csv"),
    path("dashboard/exports/xlsx/", views.export_xlsx, name="xlsx"),
    path("dashboard/exports/pdf/", views.export_pdf, name="pdf"),
]
