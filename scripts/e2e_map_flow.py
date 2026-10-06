"""
Vérification de bout en bout de la CARTE PUBLIQUE dans un vrai navigateur (Chromium via
Playwright), en format smartphone puis ordinateur.

Crée un jeu de données de test (signalements marqués « démonstration »), puis vérifie :
visibilité (Vérifié seulement), confidentialité de l'API et des popups, clustering, heatmap,
filtres, changement de statut, erreurs réseau, mise en page responsive. Tout est supprimé à la fin.

Prérequis : pip install playwright && playwright install chromium
Utilisation : python manage.py runserver 8000   puis   python scripts/e2e_map_flow.py
"""
import io
import json
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
from django.utils import timezone  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from accounts.models import User  # noqa: E402
from reports.choices import AccidentType, ReportStatus, Severity  # noqa: E402
from reports.models import AccidentReport  # noqa: E402

BASE = os.environ.get("ACCIMAP_URL", "http://localhost:8000")
SHOTS = os.environ.get("E2E_SHOTS", "e2e_screenshots")
os.makedirs(SHOTS, exist_ok=True)

results, created_ids, created_users = [], [], []
CANARY = (1.3300, 6.2800)            # (lon, lat) : lieu réservé aux signalements NON publics
SECRET_NOTE = "CANARY-NOTE-ADMIN"
PUBLIC_KEYS = {"type", "type_label", "severity", "severity_label", "date", "time", "injured", "deaths"}


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))


def tile_png():
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (229, 227, 223)).save(buffer, "PNG")
    return buffer.getvalue()


TILE = tile_png()


# ---------------------------------------------------------------- jeu de données
def seed():
    random.seed(7)
    today = timezone.localdate()
    admin = User.objects.create_user(f"9{random.randint(1000000, 9999999)}", is_staff=True)
    citizens = [User.objects.create_user(f"9{random.randint(1000000, 9999999)}") for _ in range(3)]
    created_users.extend([admin, *citizens])
    phones = [c.phone_number for c in citizens] + [c.phone_number[4:] for c in citizens]

    hotspots = [((1.2228, 6.1319), 14), ((1.2050, 6.1650), 9), ((1.2150, 6.2000), 7), ((1.3000, 6.1250), 5)]
    types, severities = list(AccidentType.values), list(Severity.values)

    def make(lon, lat, status, **extra):
        data = dict(
            accident_type=random.choice(types), severity=random.choice(severities),
            accident_date=today - timedelta(days=random.randint(1, 120)), accident_time=f"{random.randint(0, 23):02d}:{random.choice(['00', '15', '30', '45'])}",
            injured_count=random.randint(0, 5), death_count=random.choice([0, 0, 0, 1]),
            location=Point(lon, lat, srid=4326), status=status, is_demo=True,
            description=f"CANARY-DESC Jean Dupont {random.choice(phones)} TG-{random.randint(1000, 9999)}-AB",
            admin_note=SECRET_NOTE,
        )
        data.update(extra)
        report = AccidentReport.objects.create(**data)
        created_ids.append(report.pk)
        return report

    for (lon, lat), n in hotspots:
        for i in range(n):
            anonymous = random.random() < 0.4
            make(random.gauss(lon, 0.003), random.gauss(lat, 0.003), ReportStatus.VERIFIED,
                 is_anonymous=anonymous, user=None if anonymous else random.choice(citizens), verified_by=admin)
    for _ in range(5):  # signalements isolés
        make(random.uniform(1.17, 1.33), random.uniform(6.10, 6.26), ReportStatus.VERIFIED, verified_by=admin)
    for status in (ReportStatus.PENDING, ReportStatus.REJECTED):
        for _ in range(6):  # pièges : ne doivent JAMAIS apparaître
            make(CANARY[0] + random.uniform(-0.0004, 0.0004), CANARY[1] + random.uniform(-0.0004, 0.0004),
                 status, user=random.choice(citizens))
    return phones


def public_qs():
    return AccidentReport.objects.public().filter(pk__in=created_ids)


# ---------------------------------------------------------------- utilitaires navigateur
def new_page(p, viewport, mobile):
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport=viewport, has_touch=mobile, is_mobile=mobile, locale="fr-FR",
                              device_scale_factor=2 if mobile else 1)
    ctx.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(status=200, content_type="image/png", body=TILE))
    page = ctx.new_page()
    page.set_default_timeout(10000)
    page.errors, page.bad = [], []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(f"{m.text} @ {m.location.get('url')}") if m.type == "error" else None)
    page.on("response", lambda r: page.bad.append(f"{r.status} {r.url}") if r.status >= 400 and "/static/" in r.url else None)
    return browser, page


def press(page, mobile, x, y):
    page.touchscreen.tap(x, y) if mobile else page.mouse.click(x, y)


def idle(page):
    page.wait_for_timeout(450)  # délai d'anti-rebond des filtres
    page.wait_for_function("document.getElementById('map-loading').hidden && !/Chargement/.test(document.getElementById('map-count').textContent)")
    page.wait_for_timeout(300)


def shown_count(page):
    text = page.locator("#map-count").inner_text()
    m = re.match(r"(\d+)", text)
    return int(m.group(1)) if m else 0


def open_filters(page, mobile):
    if mobile and not page.locator("#filters-panel").is_visible():
        page.click("button:has-text('Filtrer les signalements')")
        page.wait_for_selector("#filters-panel.show")
        page.wait_for_timeout(400)


def rendered_total(page):
    return page.evaluate("""() => {
        const clusters = [...document.querySelectorAll('.marker-cluster')].reduce((s, c) => s + (parseInt(c.textContent, 10) || 0), 0);
        return clusters + document.querySelectorAll('.sev-marker').length;
    }""")


def drill_to_marker(page, mobile):
    """Zoome en touchant les clusters jusqu'à voir un marqueur individuel ; retourne ses coordonnées écran."""
    for _ in range(14):
        target = page.evaluate("""() => {
            const map = document.getElementById('public-map').getBoundingClientRect();
            const cx = map.left + map.width / 2, cy = map.top + map.height / 2;
            const inside = (r) => r.left > map.left + 50 && r.right < map.right - 10 && r.top > map.top + 10 && r.bottom < map.bottom - 40;
            const pick = (sel) => [...document.querySelectorAll(sel)].map(e => ({r: e.getBoundingClientRect(), s: sel}))
                .filter(o => inside(o.r)).sort((a, b) => Math.hypot(a.r.left - cx, a.r.top - cy) - Math.hypot(b.r.left - cx, b.r.top - cy))[0];
            const m = pick('.sev-marker'); if (m) return {kind: 'marker', x: m.r.left + m.r.width / 2, y: m.r.top + m.r.height / 2};
            const c = pick('.marker-cluster'); return c ? {kind: 'cluster', x: c.r.left + c.r.width / 2, y: c.r.top + c.r.height / 2} : null;
        }""")
        if not target:
            return None
        if target["kind"] == "marker":
            return target
        press(page, mobile, target["x"], target["y"])
        page.wait_for_timeout(750)
    return None


# ---------------------------------------------------------------- scénarios
def scenario_main(p, name, viewport, mobile, phones):
    print(f"\n--- [{name}] Chargement, visibilité, confidentialité, mise en page")
    browser, page = new_page(p, viewport, mobile)
    with page.expect_response(lambda r: "/map/data/" in r.url) as info:
        page.goto(BASE + "/map/")
    api = info.value
    page.wait_for_load_state("networkidle"); idle(page)
    body = api.text()
    data = json.loads(body)
    expected = public_qs().count()

    check("API : statut 200 et GeoJSON", api.status == 200 and "geo+json" in api.headers["content-type"])
    check(f"API : {expected} signalements vérifiés exactement", data["meta"]["count"] == expected == len(data["features"]), f"{data['meta']}")
    check("API : liste blanche stricte des champs", all(set(f["properties"]) == PUBLIC_KEYS and set(f) == {"type", "geometry", "properties"} for f in data["features"]))
    check("API : aucun numéro de téléphone", not any(ph in body for ph in phones) and "+228" not in body)
    check("API : aucune description / note / identifiant / référence / mode anonyme",
          all(k not in body for k in ("CANARY-DESC", SECRET_NOTE, "ACC-", "anonymous", "user", "description")))
    near_canary = [f for f in data["features"] if abs(f["geometry"]["coordinates"][0] - CANARY[0]) < 0.01 and abs(f["geometry"]["coordinates"][1] - CANARY[1]) < 0.01]
    check("API : aucun signalement en attente / rejeté (zone piège vide)", not near_canary)
    check("Compteur affiché = nombre de signalements vérifiés", shown_count(page) == expected, page.locator("#map-count").inner_text())

    w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    check("Pas de défilement horizontal", w[0] <= w[1], str(w))
    height = page.locator("#public-map").bounding_box()["height"]
    check("Carte de taille utilisable", height >= 380, str(height))
    check("Carte entièrement contenue dans la largeur de l'écran", page.locator("#public-map").bounding_box()["width"] <= viewport["width"])
    if mobile:
        check("Mobile : filtres repliés par défaut", not page.locator("#filters-panel").is_visible())
        check("Mobile : bouton « Filtrer » accessible", page.locator("button:has-text('Filtrer les signalements')").is_visible())
    else:
        check("Ordinateur : filtres visibles directement", page.locator("#filters-panel").is_visible() and page.locator("#id_accident_type").is_visible())
    sizes = page.evaluate("[...document.querySelectorAll('main .btn, main label.btn')].filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().height))")
    check("Cibles tactiles >= 44 px", sizes and all(s >= 44 for s in sizes), str(sizes))
    check("Mise en garde heatmap affichée", "zones à forte concentration de signalements" in page.locator("main").inner_text() and "preuve officielle" in page.locator("main").inner_text())
    check("Légende des gravités", all(t in page.locator(".map-legend").inner_text() for t in ("Faible", "Moyenne", "Grave", "Très grave")))
    page.screenshot(path=f"{SHOTS}/{name}_map_1_markers.png")

    print(f"--- [{name}] Clustering et popup")
    check("Clustering : des clusters sont affichés", page.locator(".marker-cluster").count() > 0)
    total = rendered_total(page)
    check("Clustering : clusters + marqueurs = total des signalements", total == expected, f"{total} vs {expected}")
    target = drill_to_marker(page, mobile)
    check("Zoom par clusters jusqu'à un marqueur individuel", target is not None)
    if target:
        press(page, mobile, target["x"], target["y"])
        page.wait_for_selector(".leaflet-popup-content")
        page.wait_for_timeout(700)  # fin de l'animation d'apparition du popup
        popup = page.locator(".leaflet-popup-content").inner_text()
        check("Popup : type, date, heure", re.search(r"\d{2}/\d{2}/\d{4} à \d{2}:\d{2}", popup) and any(t in popup for t in ("Collision", "Accident", "Renversement", "Autre")), popup)
        check("Popup : gravité, statut Vérifié, blessés, décès", "Vérifié" in popup and "Blessés :" in popup and "Décès :" in popup and re.search(r"Faible|Moyenne|Grave|Très grave", popup))
        everything = page.content() + page.locator("body").inner_text()
        check("Popup/page : aucun téléphone, description, note ou mode anonyme",
              not any(ph in everything for ph in phones) and "CANARY" not in everything and not re.search(r"anonym(e|es)\b(?! *$)", popup.lower()))
        page.screenshot(path=f"{SHOTS}/{name}_map_2_popup.png")
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    check("Aucun fichier statique manquant", not page.bad, str(page.bad))
    browser.close()


def scenario_heatmap(p, name, viewport, mobile):
    print(f"\n--- [{name}] Heatmap et couches")
    browser, page = new_page(p, viewport, mobile)
    page.goto(BASE + "/map/"); page.wait_for_load_state("networkidle"); idle(page)
    check("Heatmap désactivée par défaut", page.locator("canvas.leaflet-heatmap-layer").count() == 0)
    page.click("label[for=toggle-heat]"); page.wait_for_timeout(700)
    check("Heatmap activée : couche canvas créée", page.locator("canvas.leaflet-heatmap-layer").count() == 1)
    painted = page.evaluate("""() => { const c = document.querySelector('canvas.leaflet-heatmap-layer');
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
        for (let i = 3; i < d.length; i += 4) { if (d[i] > 0) n++; } return n; }""")
    check("Heatmap : des pixels de chaleur sont réellement dessinés", painted > 500, str(painted))
    legend = page.locator(".heat-legend").inner_text()
    check("Légende heatmap : « Concentration de signalements » faible → forte", "Concentration de signalements" in legend and "faible" in legend and "forte" in legend)
    page.screenshot(path=f"{SHOTS}/{name}_map_3_both.png")
    page.click("label[for=toggle-markers]"); page.wait_for_timeout(500)
    check("Marqueurs masqués, heatmap conservée", page.locator(".sev-marker, .marker-cluster").count() == 0 and page.locator("canvas.leaflet-heatmap-layer").count() == 1)
    page.screenshot(path=f"{SHOTS}/{name}_map_4_heat_only.png")
    page.click("label[for=toggle-markers]"); page.click("label[for=toggle-heat]"); page.wait_for_timeout(600)
    check("Réactivation des marqueurs / retrait de la heatmap", page.locator(".marker-cluster, .sev-marker").count() > 0 and page.locator("canvas.leaflet-heatmap-layer").count() == 0 and page.locator(".heat-legend").count() == 0)

    # Régression : activer PUIS désactiver la heatmap, puis changer un filtre, faisait échouer le chargement.
    open_filters(page, mobile)
    some_type = public_qs().values_list("accident_type", flat=True).first()
    expected = public_qs().filter(accident_type=some_type).count()
    page.select_option("#id_accident_type", some_type); idle(page)
    check("Après heatmap activée puis désactivée, un filtre se charge normalement", shown_count(page) == expected and page.locator("#map-alert.alert-danger").count() == 0, page.locator("#map-count").inner_text())
    page.click("label[for=toggle-heat]"); page.wait_for_timeout(700)
    painted = page.evaluate("""() => { const c = document.querySelector('canvas.leaflet-heatmap-layer'); if (!c) return -1;
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
        for (let i = 3; i < d.length; i += 4) { if (d[i] > 0) n++; } return n; }""")
    check("Heatmap réactivée après un filtre : redessinée avec les points filtrés", painted > 200, str(painted))
    page.select_option("#id_accident_type", ""); idle(page)
    check("Filtre retiré avec la heatmap active : tous les points reviennent", shown_count(page) == public_qs().count() and page.locator("canvas.leaflet-heatmap-layer").count() == 1)
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    browser.close()


def scenario_filters(p, name, viewport, mobile):
    print(f"\n--- [{name}] Filtres")
    browser, page = new_page(p, viewport, mobile)
    page.goto(BASE + "/map/"); page.wait_for_load_state("networkidle"); idle(page)
    open_filters(page, mobile)
    total = public_qs().count()

    some_type = public_qs().values_list("accident_type", flat=True).first()
    page.select_option("#id_accident_type", some_type); idle(page)
    n = public_qs().filter(accident_type=some_type).count()
    check(f"Filtre type ({some_type}) : {n} affichés", shown_count(page) == n, f"{shown_count(page)} vs {n}")
    check("L'URL conserve le filtre (partageable)", f"accident_type={some_type}" in page.url)

    page.select_option("#id_severity", "SEVERE"); idle(page)
    n2 = public_qs().filter(accident_type=some_type, severity="SEVERE").count()
    expected_text = "Aucun" if n2 == 0 else str(n2)
    check("Filtres type + gravité combinés", page.locator("#map-count").inner_text().startswith(expected_text), f"{page.locator('#map-count').inner_text()} vs {n2}")

    page.click("#filters-reset"); idle(page)
    check("Réinitialiser : tous les signalements reviennent", shown_count(page) == total and page.input_value("#id_accident_type") == "", f"{shown_count(page)} vs {total}")

    dates = sorted(public_qs().values_list("accident_date", flat=True))
    mid = dates[len(dates) // 2]
    page.fill("#id_date_from", mid.isoformat()); idle(page)
    n3 = public_qs().filter(accident_date__gte=mid).count()
    check("Filtre période (du …)", shown_count(page) == n3, f"{shown_count(page)} vs {n3}")
    page.fill("#id_date_to", mid.isoformat()); idle(page)
    n4 = public_qs().filter(accident_date=mid).count()
    check("Filtre période (un seul jour)", shown_count(page) == n4, f"{shown_count(page)} vs {n4}")

    page.click("#filters-reset"); idle(page)
    page.select_option("#id_severity", "LOW"); page.select_option("#id_accident_type", "ROLLOVER"); idle(page)
    page.fill("#id_date_from", (dates[-1]).isoformat()); page.fill("#id_date_to", (dates[-1]).isoformat()); idle(page)
    n5 = public_qs().filter(severity="LOW", accident_type="ROLLOVER", accident_date=dates[-1]).count()
    text = page.locator("#map-count").inner_text()
    check("Combinaison stricte cohérente avec la base", (n5 == 0 and "Aucun signalement vérifié ne correspond" in text) or shown_count(page) == n5, f"{text} vs {n5}")
    page.screenshot(path=f"{SHOTS}/{name}_map_5_filters.png")

    page.click("#filters-reset"); idle(page)
    page.fill("#id_date_from", "2026-09-30"); page.fill("#id_date_to", "2026-09-01"); page.wait_for_timeout(900)
    check("Période inversée : message d'erreur clair du serveur", "postérieure" in page.locator("#map-alert").inner_text())

    # Rechargement avec filtres dans l'URL
    page.goto(BASE + f"/map/?accident_type={some_type}"); page.wait_for_load_state("networkidle"); idle(page)
    check("Filtres restaurés depuis l'URL au rechargement", page.input_value("#id_accident_type") == some_type and shown_count(page) == public_qs().filter(accident_type=some_type).count())
    check("Aucun filtre « statut / mode / zone » proposé au public", page.locator("[name=status], [name=mode], [name=zone]").count() == 0)
    check("Aucune erreur JavaScript", not [e for e in page.errors if "400" not in e], str(page.errors))
    browser.close()


def scenario_status_changes(p, name, viewport, mobile):
    print(f"\n--- [{name}] Changement de statut visible sur la carte")
    browser, page = new_page(p, viewport, mobile)
    base = public_qs().count()
    pending = AccidentReport.objects.filter(pk__in=created_ids, status=ReportStatus.PENDING).first()
    verified = public_qs().first()
    admin = User.objects.get(pk=created_users[0].pk)

    pending.set_status(ReportStatus.VERIFIED, admin)
    page.goto(BASE + "/map/"); page.wait_for_load_state("networkidle"); idle(page)
    check("En attente -> Vérifié : le signalement apparaît", shown_count(page) == base + 1, f"{shown_count(page)} vs {base + 1}")

    verified.set_status(ReportStatus.REJECTED, admin)
    page.reload(); page.wait_for_load_state("networkidle"); idle(page)
    check("Vérifié -> Rejeté : le signalement disparaît", shown_count(page) == base, f"{shown_count(page)} vs {base}")

    verified.set_status(ReportStatus.PENDING, admin)
    pending.set_status(ReportStatus.PENDING, admin)
    page.reload(); page.wait_for_load_state("networkidle"); idle(page)
    check("Remise en attente : retiré de la carte", shown_count(page) == base - 1, f"{shown_count(page)} vs {base - 1}")
    verified.set_status(ReportStatus.VERIFIED, admin)
    browser.close()


def scenario_errors(p, name, viewport, mobile):
    print(f"\n--- [{name}] Erreurs réseau : API et fond de carte")
    browser, page = new_page(p, viewport, mobile)
    page.route("**/map/data/**", lambda r: r.abort())
    page.goto(BASE + "/map/"); page.wait_for_load_state("networkidle")
    page.wait_for_selector("#map-alert.alert-danger")
    check("API injoignable : message clair avec bouton « Réessayer »", "Impossible de charger" in page.locator("#map-alert").inner_text() and page.locator("#map-alert button").is_visible())
    page.unroute("**/map/data/**")
    page.click("#map-alert button"); idle(page)
    check("Réessayer : les signalements se chargent", shown_count(page) == public_qs().count())
    browser.close()

    browser, page = new_page(p, viewport, mobile)
    page.context.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(status=403))
    page.goto(BASE + "/map/"); page.wait_for_load_state("networkidle"); idle(page)
    page.wait_for_selector("#map-alert.alert-warning")
    check("Fond de carte inaccessible : utilisateur prévenu", "fond de carte" in page.locator("#map-alert").inner_text())
    check("Fond de carte inaccessible : signalements toujours affichés", shown_count(page) == public_qs().count() and page.locator(".marker-cluster, .sev-marker").count() > 0)
    browser.close()


def scenario_home(p, name, viewport, mobile):
    print(f"\n--- [{name}] Page d'accueil")
    browser, page = new_page(p, viewport, mobile)
    page.goto(BASE + "/"); page.wait_for_load_state("networkidle")
    n = public_qs().count()
    check("Accueil : chiffre réel = signalements vérifiés", page.locator("#home-stats .stat-number").inner_text().strip() == str(n), page.locator("#home-stats").inner_text())
    check("Accueil : accès signalement identifié / anonyme / connexion",
          all(page.locator(f".hero a:has-text('{t}')").count() >= 1 for t in ("Signaler un accident", "Signaler anonymement", "Se connecter")))
    check("Accueil : présentation d'ACCIMAP", page.locator("h2:has-text('Qu\\'est-ce qu\\'ACCIMAP')").is_visible())
    w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    check("Accueil : pas de défilement horizontal", w[0] <= w[1], str(w))
    page.screenshot(path=f"{SHOTS}/{name}_home.png", full_page=True)
    page.locator("main a:has-text('Ouvrir la carte')").first.click()
    page.wait_for_url(re.compile(r"/map/"))
    check("Accueil -> carte publique", page.url.endswith("/map/"))
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    browser.close()


def cleanup():
    AccidentReport.objects.filter(pk__in=created_ids).delete()
    User.objects.filter(pk__in=[u.pk for u in created_users]).delete()
    print(f"\nNettoyage : {len(created_ids)} signalement(s) et {len(created_users)} utilisateur(s) d'essai supprimés.")


if __name__ == "__main__":
    phones = seed()
    print(f"Jeu de test : {public_qs().count()} vérifiés, "
          f"{AccidentReport.objects.filter(pk__in=created_ids).exclude(status='VERIFIED').count()} non publics (pièges).")
    try:
        with sync_playwright() as p:
            for name, viewport, mobile in [("mobile", {"width": 390, "height": 844}, True),
                                           ("desktop", {"width": 1366, "height": 800}, False)]:
                print(f"\n================ {name.upper()} {viewport['width']}x{viewport['height']} ================")
                for scenario in (scenario_main, scenario_heatmap, scenario_filters, scenario_status_changes,
                                 scenario_errors, scenario_home):
                    try:
                        scenario(p, name, viewport, mobile, phones) if scenario is scenario_main else scenario(p, name, viewport, mobile)
                    except Exception:
                        check(f"{scenario.__name__} s'est exécuté sans exception", False, traceback.format_exc(limit=4))
    finally:
        cleanup()
    ok = sum(results)
    print(f"\nRÉSULTAT : {ok}/{len(results)} contrôles réussis")
    sys.exit(0 if ok == len(results) else 1)
