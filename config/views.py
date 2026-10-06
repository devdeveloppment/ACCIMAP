"""Vues techniques du projet (hors fonctionnalités métier)."""
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render


def healthz(request):
    """Sonde de santé utilisée par Render : vérifie que la base répond."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


def home(request):
    """Page d'accueil publique. Le seul chiffre affiché est public : signalements vérifiés."""
    from reports.models import AccidentReport

    return render(request, "home.html", {"verified_count": AccidentReport.objects.public().count()})


def about(request):
    """Présentation du projet."""
    return render(request, "about.html")


def permission_denied(request, exception=None):
    """Page 403 personnalisée (accès réservé aux administrateurs)."""
    return render(request, "403.html", status=403)
