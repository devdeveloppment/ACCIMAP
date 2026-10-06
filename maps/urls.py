from django.urls import path

from . import views

app_name = "maps"

urlpatterns = [
    path("map/", views.public_map, name="public_map"),
    path("map/data/", views.public_data, name="public_data"),
]
