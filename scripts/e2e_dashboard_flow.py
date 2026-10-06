"""
Vérification de bout en bout de l'ESPACE ADMINISTRATEUR dans un vrai navigateur (Chromium via Playwright).

Trois profils, chacun connecté comme en situation réelle :
  * utilisateur simple : connexion citoyenne par OTP, aucun accès ;
  * staff              : connexion PRIVÉE (identifiant + mot de passe) ; statistiques, liste, détail, photo, carte,
                         statut, note, modification, suppression ;
  * superutilisateur   : connexion PRIVÉE ; tout ce que fait le staff + gestion des utilisateurs et des permissions.
Un compte staff connecté par l'OTP citoyen est aussi vérifié : il est renvoyé vers la connexion privée.

Prérequis : pip install playwright && playwright install chromium
Utilisation : OTP_RESEND_DELAY_SECONDS=0 python manage.py runserver 8000
              (les profils citoyens se reconnectent plusieurs fois par OTP : le délai anti-renvoi de 60 s doit être
              désactivé pour CE serveur de test uniquement)
              E2E_VIEWPORT=mobile|desktop python scripts/e2e_dashboard_flow.py     (défaut : les deux)
Toutes les données de test sont supprimées à la fin.
"""
import io
import os
import random
import re
import sys
import traceback
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
import django  # noqa: E402

django.setup()

from django.contrib.gis.geos import Point  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.utils import timezone  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from accounts.models import User  # noqa: E402
from accounts.roles import ROLE_STAFF, ROLE_SUPERUSER, set_role  # noqa: E402
from reports.choices import AccidentType, ReportStatus, Severity  # noqa: E402
from reports.models import AccidentReport  # noqa: E402
from reports.zone import get_coverage_zone  # noqa: E402

BASE = os.environ.get("ACCIMAP_URL", "http://localhost:8000")
SHOTS = os.environ.get("E2E_SHOTS", "e2e_screenshots")
os.makedirs(SHOTS, exist_ok=True)

results, created_ids, created_users = [], [], []
P = {}  # profils : nom -> utilisateur


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))


def phone():
    n = "9" + "".join(random.choice("0123456789") for _ in range(7))
    return n


def tile_png():
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (229, 227, 223)).save(buffer, "PNG")
    return buffer.getvalue()


TILE = tile_png()


def jpeg_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (30, 120, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


# ---------------------------------------------------------------- jeu de données
def seed():
    assert AccidentReport.objects.count() == 0, "La base de développement doit être vide pour ce test."
    random.seed(11)
    today = timezone.localdate()
    for name, role in [("citizen", None), ("target", None), ("reporter", None), ("staff", ROLE_STAFF), ("super", ROLE_SUPERUSER)]:
        user = User.objects.create_user(phone())
        if role:
            set_role(user, role)
            user.set_password(ADMIN_PASSWORD)
            user.save()
        P[name] = User.objects.get(pk=user.pk)
        created_users.append(P[name])

    admin = P["staff"]
    types, severities = list(AccidentType.values), list(Severity.values)

    def make(lon, lat, status, **extra):
        data = dict(
            accident_type=random.choice(types), severity=random.choice(severities),
            accident_date=today - timedelta(days=random.randint(1, 90)),
            accident_time=f"{random.randint(0, 23):02d}:{random.choice(['00', '30'])}",
            injured_count=random.randint(0, 4), death_count=random.choice([0, 0, 1]),
            location=Point(lon, lat, srid=4326), status=status, is_demo=True,
            # Texte neutre : une description est du contenu saisi, légitimement visible par l'administrateur.
            description="Texte libre du déclarant", admin_note="",
        )
        data.update(extra)
        if status != ReportStatus.PENDING:
            data.update(verified_by=admin, verified_at=timezone.now())
        report = AccidentReport.objects.create(**data)
        created_ids.append(report.pk)
        return report

    spots = [(1.2228, 6.1319), (1.2050, 6.1650), (1.2150, 6.2000), (1.3000, 6.1250)]
    plan = [(ReportStatus.PENDING, 10), (ReportStatus.VERIFIED, 18), (ReportStatus.REJECTED, 5)]
    for status, n in plan:
        for i in range(n):
            lon, lat = random.choice(spots)
            anonymous = random.random() < 0.4
            make(random.gauss(lon, 0.004), random.gauss(lat, 0.004), status,
                 is_anonymous=anonymous, user=None if anonymous else P["reporter"])
    for _ in range(3):  # hors zone indicative (vers Aného)
        make(1.55 + random.uniform(-0.01, 0.01), 6.25, ReportStatus.PENDING, user=P["reporter"])

    # signalement PENDING avec photo (pour la photo protégée, la modification et la suppression)
    P["photo_report"] = make(1.2231, 6.1319, ReportStatus.PENDING, user=P["reporter"],
                             photo=SimpleUploadedFile("photo.jpg", jpeg_bytes(), content_type="image/jpeg"))
    P["to_delete"] = make(1.2240, 6.1330, ReportStatus.PENDING, is_anonymous=True)


# ---------------------------------------------------------------- utilitaires navigateur
def new_page(p, viewport, mobile):
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport=viewport, has_touch=mobile, is_mobile=mobile, locale="fr-FR",
                              device_scale_factor=2 if mobile else 1)
    ctx.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(status=200, content_type="image/png", body=TILE))
    page = ctx.new_page()
    page.set_default_timeout(12000)
    page.errors, page.bad = [], []
    page.expected_errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(f"{m.text} @ {m.location.get('url')}")
            if m.type == "error" and "403" not in m.text and "404" not in m.text else None)
    page.on("response", lambda r: page.bad.append(f"{r.status} {r.url}") if r.status >= 400 and "/static/" in r.url else None)
    return browser, page


def login(page, user, next_path="/dashboard/"):
    page.goto(f"{BASE}/login/?next={next_path}")
    page.fill("#id_phone_number", user.phone_number[4:])
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"/verify-otp/"))
    page.fill(".otp-input", page.locator("#dev-code").inner_text().strip())
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")

ADMIN_PASSWORD = "E2E-Admin-Phrase-2026"


def admin_login(page, user, next_path="/dashboard/"):
    """Connexion par l'ESPACE PRIVÉ : identifiant administrateur + mot de passe (jamais par l'OTP citoyen)."""
    page.goto(f"{BASE}/dashboard/login/?next={next_path}")
    page.fill("#id_username", user.phone_number[4:])
    page.fill("#id_password", ADMIN_PASSWORD)
    page.click("#admin-login-form button[type=submit]")
    page.wait_for_load_state("networkidle")



def no_overflow(page, label):
    w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    check(f"{label} : pas de défilement horizontal de la page", w[0] <= w[1], str(w))


def tap_targets(page, label):
    sizes = page.evaluate("""[...document.querySelectorAll('main .btn')].filter(e => e.offsetParent)
        .map(e => [e.classList.contains('btn-sm'), Math.round(e.getBoundingClientRect().height)])""")
    check(f"{label} : cibles tactiles (>= 44 px, >= 40 px pour les petits boutons)",
          sizes and all(h >= (40 if small else 44) for small, h in sizes), str(sizes))


def kpi(page, name):
    return int(page.locator(f"#kpi-{name}").inner_text().strip())


def open_filters(page):
    toggler = page.locator("button[data-bs-target='#filters-panel']")
    if toggler.count() and toggler.is_visible() and not page.locator("#filters-panel").is_visible():
        toggler.click()
        page.wait_for_selector("#filters-panel.show")
        page.wait_for_timeout(400)


def expected_stats(qs=None):
    qs = AccidentReport.objects.all() if qs is None else qs
    retained = qs.exclude(status="REJECTED")
    from django.db.models import Sum
    return {
        "total": qs.count(), "anonymous": qs.filter(is_anonymous=True).count(), "identified": qs.filter(is_anonymous=False).count(),
        "pending": qs.filter(status="PENDING").count(), "verified": qs.filter(status="VERIFIED").count(),
        "rejected": qs.filter(status="REJECTED").count(), "accidents": retained.count(),
        "injured": retained.aggregate(s=Sum("injured_count"))["s"] or 0, "deaths": retained.aggregate(s=Sum("death_count"))["s"] or 0,
    }


def chart_values(page, canvas_id):
    return page.evaluate("""(id) => { const c = Chart.getChart(document.getElementById(id));
        return c ? c.data.datasets[0].data : null; }""", canvas_id)


def canvas_painted(page, canvas_id):
    return page.evaluate("""(id) => { const c = document.getElementById(id); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let n = 0; for (let i = 3; i < d.length; i += 4) { if (d[i] > 0) n++; } return n; }""", canvas_id)


def map_idle(page, previous):
    """Attend la fin d'un rechargement de la carte : fin du chargement ET compteur mis à jour."""
    page.wait_for_function(
        """(prev) => document.getElementById('map-loading').hidden
            && document.getElementById('map-count').textContent !== prev
            && !/Chargement/.test(document.getElementById('map-count').textContent)""", arg=previous)
    page.wait_for_timeout(300)


def public_count(page):
    return page.evaluate("fetch('/map/data/').then(r => r.json()).then(d => d.meta.count)")


# ---------------------------------------------------------------- scénarios
def scenario_citizen(p, name, viewport, mobile):
    print(f"\n--- [{name}] UTILISATEUR SIMPLE : aucun accès administrateur")
    browser, page = new_page(p, viewport, mobile)
    login(page, P["citizen"], "/")
    r = page.goto(BASE + "/dashboard/")
    check("Utilisateur simple -> /dashboard/ : 403", r.status == 403)
    check("Message clair d'accès refusé", "droits nécessaires" in page.locator("main").inner_text())
    page.screenshot(path=f"{SHOTS}/{name}_dash_0_citizen_403.png")
    for path in ["/dashboard/reports/", "/dashboard/statistics/", "/dashboard/map/", "/dashboard/users/",
                 f"/dashboard/reports/{P['photo_report'].pk}/", f"/dashboard/reports/{P['photo_report'].pk}/photo/"]:
        check(f"Utilisateur simple -> {path.split(str(P['photo_report'].pk))[-1] or path} : 403", page.goto(BASE + path).status == 403)
    page.goto(BASE + "/")
    check("Aucun lien « Tableau de bord » pour un utilisateur simple", page.locator("a:has-text('Tableau de bord')").count() == 0)
    status = page.evaluate("fetch('/dashboard/map/data/').then(r => r.status)")
    check("API de la carte admin -> 403 pour un utilisateur simple", status == 403)
    status = page.evaluate(f"fetch('/media/{P['photo_report'].photo.name}').then(r => r.status)")
    check("Fichier photo via /media/ -> 404 (aucune URL publique)", status == 404)
    status = page.evaluate("fetch('/dashboard/reports/%s/delete/', {method: 'POST'}).then(r => r.status)" % P["to_delete"].pk)
    check("Suppression forgée par un utilisateur simple : refusée", status in (403,) and AccidentReport.objects.filter(pk=P["to_delete"].pk).exists())
    browser.close()


def scenario_staff(p, name, viewport, mobile):
    print(f"\n--- [{name}] STAFF : tableau de bord, statistiques, liste")
    browser, page = new_page(p, viewport, mobile)
    admin_login(page, P["staff"])
    page.goto(BASE + "/dashboard/"); page.wait_for_load_state("networkidle"); page.wait_for_timeout(1300)
    e = expected_stats()
    check("Accès au tableau de bord (200)", "Tableau de bord" in page.locator("h1").inner_text())
    check("Chiffres = base de données (total, anonymes, identifiés)",
          (kpi(page, "total"), kpi(page, "anonymous"), kpi(page, "identified")) == (e["total"], e["anonymous"], e["identified"]), f"{e}")
    check("Chiffres = base de données (en attente, vérifiés, rejetés)",
          (kpi(page, "pending"), kpi(page, "verified"), kpi(page, "rejected")) == (e["pending"], e["verified"], e["rejected"]))
    check("Blessés et décès (hors rejetés) = base de données", (kpi(page, "injured"), kpi(page, "deaths")) == (e["injured"], e["deaths"]), f"{kpi(page, 'injured')},{kpi(page, 'deaths')} vs {e}")
    check("Graphique statuts = base", chart_values(page, "chart-statuses") == [e["pending"], e["verified"], e["rejected"]])
    check("Graphique par période : somme = accidents retenus", sum(chart_values(page, "chart-period")) == e["accidents"])
    check("Graphique par type : somme = accidents retenus", sum(chart_values(page, "chart-types")) == e["accidents"])
    check("Graphiques réellement dessinés (Chart.js)", all(canvas_painted(page, c) > 800 for c in ("chart-period", "chart-statuses", "chart-types")))
    check("File « À traiter » affichée avec action", page.locator("#pending-queue a:has-text('Examiner')").count() >= 1)
    check("Onglet « Utilisateurs » absent pour le staff", page.locator(".dash-nav a:has-text('Utilisateurs')").count() == 0)
    no_overflow(page, "Vue d'ensemble"); tap_targets(page, "Vue d'ensemble")
    page.screenshot(path=f"{SHOTS}/{name}_dash_1_overview.png", full_page=True)
    check("Staff -> /dashboard/users/ : 403", page.goto(BASE + "/dashboard/users/").status == 403)

    # --- Statistiques filtrées via l'interface
    page.goto(BASE + "/dashboard/statistics/"); page.wait_for_load_state("networkidle"); open_filters(page)
    page.select_option("#id_status", "VERIFIED"); page.click("#stats-filters button[type=submit]")
    page.wait_for_load_state("networkidle"); page.wait_for_timeout(1300)
    ev = expected_stats(AccidentReport.objects.filter(status="VERIFIED"))
    check("Statistiques filtrées (statut = Vérifié) = base", (kpi(page, "total"), kpi(page, "injured"), kpi(page, "deaths")) == (ev["total"], ev["injured"], ev["deaths"]))
    check("Graphique filtré cohérent", sum(chart_values(page, "chart-types")) == ev["accidents"])
    open_filters(page)
    page.select_option("#id_status", ""); page.select_option("#id_mode", "anonymous"); page.click("#stats-filters button[type=submit]")
    page.wait_for_load_state("networkidle")
    check("Statistiques filtrées (mode = anonyme) = base", kpi(page, "total") == AccidentReport.objects.filter(is_anonymous=True).count())
    page.goto(BASE + "/dashboard/statistics/?status=REJECTED"); page.wait_for_load_state("networkidle")
    er = expected_stats(AccidentReport.objects.filter(status="REJECTED"))
    check("Filtre « Rejeté » : les rejetés sont inclus dans les chiffres", kpi(page, "injured") == AccidentReport.objects.filter(status="REJECTED").aggregate(s=__import__("django.db.models", fromlist=["Sum"]).Sum("injured_count"))["s"])
    no_overflow(page, "Statistiques")
    page.goto(BASE + "/dashboard/statistics/"); page.wait_for_load_state("networkidle"); page.wait_for_timeout(1300)
    page.screenshot(path=f"{SHOTS}/{name}_dash_2_statistics.png", full_page=True)

    print(f"--- [{name}] STAFF : liste, recherche, filtres")
    page.goto(BASE + "/dashboard/reports/"); page.wait_for_load_state("networkidle")
    total = AccidentReport.objects.count()
    check("Liste : tous les statuts visibles, total exact", f"{total} signalements" in page.locator("#list-count").inner_text())
    check("Liste : pagination (20 par page)", page.locator("#report-table tbody tr").count() == 20 and "Page 1 sur 2" in page.locator("main").inner_text())
    no_overflow(page, "Liste"); tap_targets(page, "Liste")
    page.screenshot(path=f"{SHOTS}/{name}_dash_3_list.png")
    open_filters(page)
    zone = get_coverage_zone()

    def apply(**fields):
        page.goto(BASE + "/dashboard/reports/"); open_filters(page)
        for field, value in fields.items():
            page.select_option(f"#id_{field}", value) if field in ("status", "mode", "zone", "accident_type", "severity") else page.fill(f"#id_{field}", value)
        page.click("#list-filters button[type=submit]"); page.wait_for_load_state("networkidle")
        m = re.match(r"(\d+)", page.locator("#list-count").inner_text().strip())
        return int(m.group(1)) if m else 0

    check("Filtre statut = En attente", apply(status="PENDING") == AccidentReport.objects.filter(status="PENDING").count())
    check("Filtre mode = Anonymes", apply(mode="anonymous") == AccidentReport.objects.filter(is_anonymous=True).count())
    check("Filtre zone = Hors zone (calculé dynamiquement)", apply(zone="outside") == AccidentReport.objects.exclude(location__within=zone.geometry).count() == 3)
    check("Filtre zone = Dans la zone", apply(zone="inside") == AccidentReport.objects.filter(location__within=zone.geometry).count())
    some_type = AccidentReport.objects.values_list("accident_type", flat=True).first()
    check("Filtre type", apply(accident_type=some_type) == AccidentReport.objects.filter(accident_type=some_type).count())
    check("Filtre gravité", apply(severity="SEVERE") == AccidentReport.objects.filter(severity="SEVERE").count())
    mid = sorted(AccidentReport.objects.values_list("accident_date", flat=True))[total // 2]
    check("Filtre période", apply(date_from=mid.isoformat()) == AccidentReport.objects.filter(accident_date__gte=mid).count())
    check("Combinaison statut + mode + zone", apply(status="PENDING", mode="identified", zone="inside") ==
          AccidentReport.objects.filter(status="PENDING", is_anonymous=False, location__within=zone.geometry).count())
    ref = AccidentReport.objects.filter(status="REJECTED").first().reference
    page.goto(BASE + "/dashboard/reports/"); open_filters(page)
    page.fill("#id_q", ref); page.click("#list-filters button[type=submit]"); page.wait_for_load_state("networkidle")
    check("Recherche par référence", page.locator("#report-table tbody tr").count() == 1 and ref in page.locator("#report-table").inner_text())
    page.goto(BASE + "/dashboard/reports/?page=2"); page.wait_for_load_state("networkidle")
    check("Page 2 de la liste", page.locator("#report-table tbody tr").count() == total - 20)
    page.goto(BASE + "/dashboard/reports/?status=ZZZ"); page.wait_for_load_state("networkidle")
    check("Filtre invalide : erreur explicite, aucun résultat", "Filtres invalides" in page.locator("main").inner_text() and page.locator("#report-table tbody tr td.text-muted").count() == 1)
    check("Liste : aucun numéro de téléphone complet", not re.search(r"\+228\d{8}|(?<!\d)9\d{7}(?!\d)", page.locator("main").inner_text()))
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    check("Aucun fichier statique manquant", not page.bad, str(page.bad))
    browser.close()


def scenario_staff_detail(p, name, viewport, mobile):
    print(f"\n--- [{name}] STAFF : détail, photo, statut, note, modification, suppression")
    browser, page = new_page(p, viewport, mobile)
    admin_login(page, P["staff"])
    rep = P["photo_report"]
    page.goto(f"{BASE}/dashboard/reports/{rep.pk}/"); page.wait_for_load_state("networkidle")
    text = page.locator("main").inner_text()
    check("Détail : référence, type, coordonnées avec 6 décimales", rep.reference in text and re.search(r"\d+\.\d{6}", text))
    check("Détail : badge de zone calculé", page.locator("main .badge:has-text('Dans la zone')").count() >= 1)
    check("Détail : mini-carte Leaflet", page.locator("#detail-map .leaflet-pane").count() > 0 and page.locator("#detail-map .leaflet-marker-icon").count() == 1)
    check("Détail : photo chargée via la vue protégée", page.evaluate("() => { const i = document.getElementById('report-photo'); return i && i.complete && i.naturalWidth > 0; }"))
    check("Détail : la photo ne passe pas par /media/", "/media/" not in page.locator("#report-photo").get_attribute("src"))
    check("Détail : déclarant masqué, jamais le numéro complet", re.search(r"\+228\*{4}\d{2}", page.locator("#declarant").inner_text()) and P["reporter"].phone_number[4:] not in page.content())
    check("Détail : pas de lien vers le compte pour le staff", page.locator("#declarant a").count() == 0)
    no_overflow(page, "Détail"); tap_targets(page, "Détail")
    page.screenshot(path=f"{SHOTS}/{name}_dash_4_detail.png", full_page=True)

    public_before = public_count(page)
    page.fill("#id_admin_note", "Vérifié avec le commissariat")
    page.click("button[name=status][value=VERIFIED]")
    page.wait_for_selector(".alert-success")
    check("Statut -> Vérifié : message et badge", "Statut mis à jour : Vérifié." in page.locator(".alert-success").inner_text() and "Vérifié" in page.locator("#status-badge").inner_text())
    rep.refresh_from_db()
    check("BASE : statut, vérificateur, date et note enregistrés", rep.status == "VERIFIED" and rep.verified_by_id == P["staff"].pk and rep.verified_at and rep.admin_note == "Vérifié avec le commissariat")
    check("Le signalement vérifié apparaît sur la carte PUBLIQUE", public_count(page) == public_before + 1)
    page.click("button[name=status][value=REJECTED]"); page.wait_for_selector(".alert-success")
    check("Statut -> Rejeté : retiré de la carte publique", public_count(page) == public_before and "Rejeté" in page.locator("#status-badge").inner_text())
    page.fill("#id_admin_note", "Note ajoutée seule")
    page.click("button:has-text('Enregistrer la note seulement')"); page.wait_for_selector(".alert-success")
    rep.refresh_from_db()
    check("Note seule : statut inchangé, note enregistrée", rep.status == "REJECTED" and rep.admin_note == "Note ajoutée seule" and "Note enregistrée." in page.locator(".alert-success").inner_text())
    page.click("button[name=status][value=PENDING]"); page.wait_for_selector(".alert-success")
    rep.refresh_from_db()
    check("Remise en attente : vérificateur effacé", rep.status == "PENDING" and rep.verified_by_id is None)

    page.click("#edit-link"); page.wait_for_url(re.compile(r"/edit/"))
    page.select_option("#id_accident_type", "ROLLOVER"); page.fill("#id_injured_count", "7"); page.fill("#id_description", "Corrigé par le staff")
    page.click("button:has-text('Enregistrer les modifications')"); page.wait_for_selector(".alert-success")
    rep.refresh_from_db()
    check("Modification enregistrée (type, blessés, description)", (rep.accident_type, rep.injured_count, rep.description) == ("ROLLOVER", 7, "Corrigé par le staff"))
    check("Modification : position, anonymat et déclarant intacts", (round(rep.location.y, 4), round(rep.location.x, 4)) == (6.1319, 1.2231) and not rep.is_anonymous and rep.user_id == P["reporter"].pk)
    page.click("#edit-link"); page.fill("#id_injured_count", "-3")
    page.evaluate("document.querySelector('form.card').noValidate = true")  # teste la validation SERVEUR, pas celle du navigateur
    page.click("button:has-text('Enregistrer les modifications')")
    check("Modification invalide refusée avec message", "/edit/" in page.url and page.locator(".invalid-feedback").count() >= 1)
    page.goto(f"{BASE}/dashboard/reports/{rep.pk}/edit/"); page.check("#id_remove_photo")
    path = Path(rep.photo.path)
    page.click("button:has-text('Enregistrer les modifications')"); page.wait_for_selector(".alert-success")
    rep.refresh_from_db()
    check("Suppression de la photo : référence et fichier effacés", not rep.photo and not path.exists())

    gone = P["to_delete"]
    page.goto(f"{BASE}/dashboard/reports/{gone.pk}/"); page.click("#delete-link"); page.wait_for_url(re.compile(r"/delete/"))
    check("Suppression : page de confirmation (rien n'est encore supprimé)", "Supprimer ce signalement ?" in page.locator("h1").inner_text() and AccidentReport.objects.filter(pk=gone.pk).exists())
    page.screenshot(path=f"{SHOTS}/{name}_dash_5_delete.png")
    page.click("#cancel-delete"); page.wait_for_url(re.compile(rf"/{gone.pk}/$"))
    check("Annuler : retour au signalement, toujours présent", AccidentReport.objects.filter(pk=gone.pk).exists())
    page.click("#delete-link"); page.click("#confirm-delete"); page.wait_for_url(re.compile(r"/dashboard/reports/$"))
    check("Confirmation : signalement supprimé définitivement + message", not AccidentReport.objects.filter(pk=gone.pk).exists() and "supprimé définitivement" in page.locator(".alert-success").inner_text())
    created_ids.remove(gone.pk)
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    browser.close()


def scenario_admin_map(p, name, viewport, mobile):
    print(f"\n--- [{name}] STAFF : carte administrateur (tous les statuts)")
    browser, page = new_page(p, viewport, mobile)
    admin_login(page, P["staff"])
    with page.expect_response(lambda r: "/dashboard/map/data/" in r.url) as info:
        page.goto(BASE + "/dashboard/map/")
    body = info.value.json()
    page.wait_for_load_state("networkidle"); page.wait_for_function("!/Chargement/.test(document.getElementById('map-count').textContent)")
    total = AccidentReport.objects.count()
    statuses = {f["properties"]["status"] for f in body["features"]}
    check("Carte admin : TOUS les statuts affichés", statuses == {"PENDING", "VERIFIED", "REJECTED"} and body["meta"]["count"] == total, f"{statuses} {body['meta']}")
    raw = str(body)
    check("Carte admin : aucun numéro, description ni note dans les données", P["reporter"].phone_number[4:] not in raw and "Texte libre" not in raw and "admin_note" not in raw)
    check("Compteur = nombre de signalements en base", page.locator("#map-count").inner_text().startswith(str(total)), page.locator("#map-count").inner_text())
    check("Marqueurs colorés par statut", page.locator(".st-pending, .st-verified, .st-rejected").count() > 0 or page.locator(".marker-cluster").count() > 0)
    check("Légende des statuts", all(t in page.locator(".map-legend").inner_text() for t in ("En attente", "Vérifié", "Rejeté")))
    no_overflow(page, "Carte admin"); tap_targets(page, "Carte admin")
    h = page.locator("#public-map").bounding_box()["height"]
    check("Carte admin de taille utilisable", h >= 380, str(h))
    page.screenshot(path=f"{SHOTS}/{name}_dash_6_map.png")

    page.click("label[for=toggle-heat]"); page.wait_for_timeout(700)
    check("Concentrations (heatmap) dessinées", page.locator("canvas.leaflet-heatmap-layer").count() == 1)
    page.click("label[for=toggle-heat]")

    open_filters(page)
    before = page.locator("#map-count").inner_text()
    page.select_option("#id_status", "PENDING"); map_idle(page, before)
    n = AccidentReport.objects.filter(status="PENDING").count()
    check("Filtre statut sur la carte admin", page.locator("#map-count").inner_text().startswith(str(n)), page.locator("#map-count").inner_text())
    before = page.locator("#map-count").inner_text()
    page.select_option("#id_status", ""); page.select_option("#id_zone", "outside"); map_idle(page, before)
    check("Filtre zone (hors zone) sur la carte admin", page.locator("#map-count").inner_text().startswith("3"), page.locator("#map-count").inner_text())
    before = page.locator("#map-count").inner_text()
    page.click("#filters-reset"); map_idle(page, before)

    # Popup administrateur : descendre jusqu'à un marqueur (on fait défiler jusqu'à la carte, comme un utilisateur)
    page.locator("#public-map").scroll_into_view_if_needed(); page.wait_for_timeout(400)
    for _ in range(14):
        target = page.evaluate("""() => {
            const map = document.getElementById('public-map').getBoundingClientRect();
            const cx = map.left + map.width / 2, cy = map.top + map.height / 2;
            const inside = (r) => r.left > map.left + 50 && r.right < map.right - 10 && r.top > map.top + 10 && r.bottom < map.bottom - 40;
            const pick = (sel) => [...document.querySelectorAll(sel)].map(e => e.getBoundingClientRect()).filter(inside)
                .sort((a, b) => Math.hypot(a.left - cx, a.top - cy) - Math.hypot(b.left - cx, b.top - cy))[0];
            const m = pick('.sev-marker'); if (m) return {kind: 'marker', x: m.left + m.width / 2, y: m.top + m.height / 2};
            const c = pick('.marker-cluster'); return c ? {kind: 'cluster', x: c.left + c.width / 2, y: c.top + c.height / 2} : null; }""")
        if not target:
            break
        (page.touchscreen.tap if mobile else page.mouse.click)(target["x"], target["y"])
        page.wait_for_timeout(750)
        if target["kind"] == "marker":
            break
    page.wait_for_selector(".leaflet-popup-content"); page.wait_for_timeout(600)
    popup = page.locator(".leaflet-popup-content").inner_text()
    check("Popup admin : référence, statut, mode, zone, lien vers le détail",
          re.search(r"ACC-\d{4}-\d{6}", popup) and re.search(r"En attente|Vérifié|Rejeté", popup) and "Mode :" in popup and "Zone :" in popup and "Ouvrir le détail" in popup)
    check("Popup admin : aucun numéro de téléphone", not re.search(r"\+228|(?<!\d)9\d{7}(?!\d)", popup))
    page.screenshot(path=f"{SHOTS}/{name}_dash_7_map_popup.png")
    page.click(".leaflet-popup-content a:has-text('Ouvrir le détail')"); page.wait_for_url(re.compile(r"/dashboard/reports/[0-9a-f-]{36}/$"))
    check("Popup -> page de détail du signalement", "Informations" in page.locator("main").inner_text())
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    browser.close()


def scenario_superuser(p, name, viewport, mobile):
    print(f"\n--- [{name}] SUPERUTILISATEUR : utilisateurs, permissions, accès")
    browser, page = new_page(p, viewport, mobile)
    admin_login(page, P["super"])
    check("Superutilisateur : tableau de bord accessible", page.goto(BASE + "/dashboard/").status == 200)
    check("Onglet « Utilisateurs » visible", page.locator(".dash-nav a:has-text('Utilisateurs')").count() == 1)
    page.goto(BASE + "/dashboard/users/"); page.wait_for_load_state("networkidle")
    target = P["target"]
    table = page.locator("#user-table").inner_text()
    check("Liste : numéros complets visibles (espace autorisé)", target.phone_number in table and P["staff"].phone_number in table)
    check("Liste : niveaux d'accès affichés", all(t in table for t in ("Utilisateur", "Staff", "Superutilisateur")))
    no_overflow(page, "Liste des utilisateurs"); tap_targets(page, "Liste des utilisateurs")
    page.screenshot(path=f"{SHOTS}/{name}_dash_8_users.png")
    page.fill("#q", target.phone_number[4:]); page.click("#user-filters button[type=submit]"); page.wait_for_load_state("networkidle")
    check("Recherche d'un utilisateur par numéro", page.locator("#user-table tbody tr").count() == 1)
    page.click("#user-table a:has-text('Gérer')"); page.wait_for_url(re.compile(r"/dashboard/users/\d+/$"))

    # Promotion en staff avec le seul droit de modifier
    page.check("input[name=role][value=staff]")
    check("Droits détaillés affichés pour le rôle Staff", page.locator("#staff-permissions").is_visible())
    page.uncheck("#id_can_delete"); page.check("#id_can_change")
    page.screenshot(path=f"{SHOTS}/{name}_dash_9_user_detail.png", full_page=True)
    page.click("#access-form button[type=submit]"); page.wait_for_selector(".alert-success")
    t = User.objects.get(pk=target.pk)
    check("Promotion en staff : voir + modifier, sans suppression", t.is_staff and not t.is_superuser and t.has_perm("reports.view_accidentreport") and t.has_perm("reports.change_accidentreport") and not t.has_perm("reports.delete_accidentreport"))
    page.check("input[name=role][value=user]")
    check("Droits détaillés masqués hors rôle Staff", page.locator("#staff-permissions").is_hidden())
    page.check("input[name=role][value=staff]")

    # Report detail vu par le superutilisateur : lien vers le compte, mais pas le numéro
    rep = AccidentReport.objects.filter(user=P["reporter"]).first()
    page.goto(f"{BASE}/dashboard/reports/{rep.pk}/")
    check("Détail (superutilisateur) : lien « Voir le compte » mais aucun numéro complet", page.locator("#declarant a:has-text('Voir le compte')").count() == 1 and P["reporter"].phone_number[4:] not in page.content())
    check("Détail (superutilisateur) : actions de modification et suppression", page.locator("#edit-link").count() == 1 and page.locator("#delete-link").count() == 1)

    # Page de son propre compte : pas de formulaire
    page.goto(f"{BASE}/dashboard/users/{P['super'].pk}/")
    check("Son propre compte : pas de formulaire (anti-verrouillage)", page.locator("#access-form").count() == 0 and "propre compte" in page.locator("main").inner_text())

    # Désactivation -> le compte ne peut plus se connecter
    page.goto(f"{BASE}/dashboard/users/{target.pk}/")
    page.uncheck("#id_is_active"); page.click("#access-form button[type=submit]"); page.wait_for_selector(".alert-success")
    check("Désactivation enregistrée", not User.objects.get(pk=target.pk).is_active)
    browser2, page2 = new_page(p, viewport, mobile)
    page2.goto(f"{BASE}/login/"); page2.fill("#id_phone_number", target.phone_number[4:]); page2.click("button[type=submit]")
    page2.wait_for_url(re.compile(r"/verify-otp/")); page2.fill(".otp-input", page2.locator("#dev-code").inner_text().strip()); page2.click("button[type=submit]")
    page2.wait_for_selector(".alert-danger")
    check("Compte désactivé : connexion refusée avec message clair", "désactivé" in page2.locator(".alert-danger").inner_text())
    browser2.close()

    # Réactivation, puis le staff promu (sans droit de suppression) se connecte
    page.check("#id_is_active"); page.click("#access-form button[type=submit]"); page.wait_for_selector(".alert-success")
    check("Réactivation enregistrée", User.objects.get(pk=target.pk).is_active)
    # Sans mot de passe d'administration, le staff promu ne peut pas ouvrir l'espace privé.
    browser3, page3 = new_page(p, viewport, mobile)
    admin_login(page3, User.objects.get(pk=target.pk))
    check("Staff promu SANS mot de passe : connexion privée refusée", "/dashboard/login/" in page3.url and "incorrect" in page3.locator("#login-error").inner_text())
    login(page3, User.objects.get(pk=target.pk), "/")                     # connexion citoyenne par OTP
    check("Staff connecté par OTP : renvoyé vers la connexion privée", page3.goto(BASE + "/dashboard/").ok and "/dashboard/login/" in page3.url)
    check("Staff connecté par OTP : aucun lien d'administration dans l'interface publique", page3.goto(BASE + "/").ok and page3.locator("a:has-text('Tableau de bord')").count() == 0 and "/dashboard/" not in page3.content())
    page.goto(f"{BASE}/dashboard/users/{target.pk}/")
    page.fill("#id_new_password1", ADMIN_PASSWORD); page.fill("#id_new_password2", ADMIN_PASSWORD)
    page.click("#set-password-form button[type=submit]"); page.wait_for_selector(".alert-success")
    check("Superutilisateur : mot de passe d'administration défini", "Un mot de passe est défini" in page.locator("#password-status").inner_text())
    admin_login(page3, User.objects.get(pk=target.pk))
    check("Staff promu : accès au tableau de bord après connexion privée", page3.goto(BASE + "/dashboard/").status == 200)
    check("Espace privé : en-tête d'administration, aucune barre citoyenne", page3.locator("#admin-header").count() == 1 and page3.locator(".accimap-navbar").count() == 0)
    page3.goto(f"{BASE}/dashboard/reports/{rep.pk}/")
    check("Staff sans droit de suppression : bouton « Supprimer » absent, « Modifier » présent", page3.locator("#delete-link").count() == 0 and page3.locator("#edit-link").count() == 1)
    check("Staff sans droit de suppression : URL de suppression -> 403", page3.goto(f"{BASE}/dashboard/reports/{rep.pk}/delete/").status == 403)
    check("Staff promu : gestion des utilisateurs -> 403", page3.goto(BASE + "/dashboard/users/").status == 403)

    # Rétrogradation : perte d'accès immédiate
    page.goto(f"{BASE}/dashboard/users/{target.pk}/"); page.check("input[name=role][value=user]")
    page.click("#access-form button[type=submit]"); page.wait_for_selector(".alert-success")
    r = page3.goto(BASE + "/dashboard/")
    # Rétrograder retire le mot de passe : Django invalide la session (déconnexion) -> connexion privée, ou 403.
    check("Rétrogradation : accès au tableau de bord retiré immédiatement", r.status == 403 or "/dashboard/login/" in page3.url, f"{r.status} {page3.url}")
    check("Rétrogradation : le compte ne peut plus se reconnecter à l'espace privé", (admin_login(page3, User.objects.get(pk=target.pk)) or True) and "/dashboard/login/" in page3.url)
    browser3.close()
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    browser.close()


def cleanup():
    from django.contrib.admin.models import LogEntry
    reports = AccidentReport.objects.filter(pk__in=created_ids)
    for report in reports:
        if report.photo:
            Path(report.photo.path).unlink(missing_ok=True)
    n = reports.delete()[0]
    LogEntry.objects.filter(user__in=[u.pk for u in created_users]).delete()
    from accounts.models import OTPCode
    OTPCode.objects.filter(phone_number__in=[u.phone_number for u in created_users]).delete()
    User.objects.filter(pk__in=[u.pk for u in created_users]).delete()
    print(f"\nNettoyage : {n} objet(s) et {len(created_users)} utilisateur(s) d'essai supprimés.")


if __name__ == "__main__":
    which = os.environ.get("E2E_VIEWPORT", "both")
    suites = [("mobile", {"width": 390, "height": 844}, True), ("desktop", {"width": 1366, "height": 800}, False)]
    suites = [s for s in suites if which in ("both", s[0])]
    seed()
    print(f"Jeu de test : {AccidentReport.objects.count()} signalements, {len(created_users)} comptes.")
    try:
        with sync_playwright() as p:
            for name, viewport, mobile in suites:
                print(f"\n================ {name.upper()} {viewport['width']}x{viewport['height']} ================")
                for scenario in (scenario_citizen, scenario_staff, scenario_staff_detail, scenario_admin_map, scenario_superuser):
                    try:
                        scenario(p, name, viewport, mobile)
                    except Exception:
                        check(f"{scenario.__name__} s'est exécuté sans exception", False, traceback.format_exc(limit=3))
    finally:
        cleanup()
    ok = sum(results)
    print(f"\nRÉSULTAT : {ok}/{len(results)} contrôles réussis")
    sys.exit(0 if ok == len(results) else 1)
