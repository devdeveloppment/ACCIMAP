"""
Vérification de bout en bout du PARCOURS DE SIGNALEMENT EN 4 ÉTAPES dans un vrai navigateur
(Chromium via Playwright), en format smartphone puis ordinateur :

    1. Type d'accident (cartes)   2. Lieu (carte, GPS automatique, confirmation)
    3. Détails (compteurs, photo)  4. Récapitulatif et envoi

Couvre : parcours identifié (connexion OTP) et anonyme ; GPS autorisé / refusé / indisponible / position
introuvable / délai dépassé / peu précis ; sélection manuelle ; lat-lon réellement enregistrées ; position hors
zone (avertissement + confirmation) ; photo avec EXIF ; validation SERVEUR (réouverture de l'étape en erreur) ;
retour du navigateur ; repli SANS JavaScript ; mise en page ; absence d'emoji ; contrôle DIRECT en base.

Prérequis : pip install playwright && playwright install chromium
Utilisation : python manage.py runserver 8000   puis   python scripts/e2e_report_flow.py
Les tuiles OpenStreetMap et le service de quartier sont simulés (aucun accès réseau requis).
"""
import io
import os
import random
import re
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
import django  # noqa: E402

django.setup()

from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from accounts.models import OTPCode, User  # noqa: E402
from reports.models import AccidentReport  # noqa: E402

BASE = os.environ.get("ACCIMAP_URL", "http://localhost:8000")
SHOTS = os.environ.get("E2E_SHOTS", "e2e_screenshots")
os.makedirs(SHOTS, exist_ok=True)

LOME = {"latitude": 6.1319, "longitude": 1.2228}
PLACE = "Hédzranawoé, Lomé"
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F]")
results, created_refs, created_phones = [], [], []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))


def fresh_phone():
    n = "9" + "".join(random.choice("0123456789") for _ in range(7))
    created_phones.append("+228" + n)
    return f"{n[:2]} {n[2:4]} {n[4:6]} {n[6:]}"


def tile_png():
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (229, 227, 223)).save(buffer, "PNG")
    return buffer.getvalue()


TILE = tile_png()


def make_photo(directory):
    """JPEG contenant GPS + appareil + auteur : doit être entièrement nettoyé."""
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x013B] = "Apple", "iPhone 15 Pro", "Jean Dupont"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (6.0, 7.0, 54.0), "E", (1.0, 13.0, 22.0)
    path = Path(directory) / "IMG_0042_Jean_Dupont.jpg"
    Image.new("RGB", (800, 600), (30, 120, 200)).save(path, "JPEG", exif=exif)
    return str(path)


def new_page(p, viewport, mobile, geolocation=True, init_script=None, accuracy=15, place=PLACE, js=True):
    browser = p.chromium.launch()
    options = dict(viewport=viewport, has_touch=mobile, is_mobile=mobile, locale="fr-FR",
                   device_scale_factor=2 if mobile else 1, java_script_enabled=js)
    if geolocation:
        options.update(geolocation={**LOME, "accuracy": accuracy}, permissions=["geolocation"])
    ctx = browser.new_context(**options)
    if init_script:
        ctx.add_init_script(init_script)
    ctx.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(status=200, content_type="image/png", body=TILE))
    ctx.route("**/report/place/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                         body='{"place": %s}' % (f'"{place}"' if place else "null")))
    page = ctx.new_page()
    page.set_default_timeout(10000)
    page.errors, page.bad = [], []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(f"{m.text} @ {m.location.get('url')}") if m.type == "error" else None)
    page.on("response", lambda r: page.bad.append(f"{r.status} {r.url}") if r.status >= 400 and "/static/" in r.url else None)
    return browser, page


def tap_or_click(page, mobile, selector, position=None):
    loc = page.locator(selector)
    kwargs = {"position": position} if position else {}
    loc.tap(**kwargs) if mobile else loc.click(**kwargs)


def step(page):
    return int(page.locator(".report-step.is-active").get_attribute("data-step"))


def wait_step(page, n):
    page.wait_for_selector(f".report-step.is-active[data-step='{n}']")
    page.wait_for_timeout(350)       # fin de l'animation d'entrée


def no_overflow(page, label):
    w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    check(f"{label} : pas de défilement horizontal", w[0] <= w[1], f"{w[0]} > {w[1]}")


def tap_targets_ok(page, label):
    sizes = page.evaluate("""[...document.querySelectorAll('.report-step.is-active .btn, .report-step.is-active .type-card, .report-step.is-active .counter-btn')]
        .filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().height))""")
    check(f"{label} : cibles tactiles >= 44 px", sizes and all(s >= 44 for s in sizes), str(sizes))


def sticky_bar_visible(page, viewport, selector, label):
    box = page.locator(selector).bounding_box()
    ok = box and box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"] + 1
    check(f"{label} : bouton d'action visible sans défiler (barre fixée en bas)", ok, str(box))


def login_via_otp(page):
    page.wait_for_url(re.compile(r"/login/\?next="))
    page.fill("#id_phone_number", fresh_phone())
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"/verify-otp/"))
    page.fill(".otp-input", page.locator("#dev-code").inner_text().strip())
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"/report/"))


def record(page):
    ref = page.locator("#report-reference").inner_text().strip()
    created_refs.append(ref)
    return AccidentReport.objects.get(reference=ref)


def pick_type(page, mobile, value="COLLISION"):
    tap_or_click(page, mobile, f"label[for='type-{value}']")
    wait_step(page, 2)


def click_map(page, mobile, dx=20, dy=10):
    box = page.locator("#location-map").bounding_box()
    tap_or_click(page, mobile, "#location-map", {"x": box["width"] / 2 + dx, "y": box["height"] / 2 + dy})
    page.wait_for_function("document.getElementById('id_latitude').value !== ''")


def bump(page, field_id, times=1, delta=1):
    """Touche « + » (ou « − ») du compteur associé au champ, comme un utilisateur."""
    for _ in range(times):
        page.locator(f".counter:has(#{field_id}) [data-delta='{delta}']").click()


def fill_details(page, photo=None, vehicles=2):
    page.fill("#id_description", "Collision au carrefour, circulation bloquée.")
    page.click("label.btn:has-text('Grave') >> nth=0")
    if vehicles:                                  # facultatif : vehicles=0 laisse le champ vide
        bump(page, "id_vehicle_count", vehicles)
    bump(page, "id_injured_count", 3)
    bump(page, "id_death_count", 1)
    if photo:
        page.set_input_files("#id_photo", photo)


def go_to_recap(page, mobile):
    page.click("#step3-next")
    wait_step(page, 4)


def no_emoji(page, label):
    check(f"{label} : aucun emoji dans l'interface", not EMOJI.search(page.inner_text("body")))


# --------------------------------------------------------------------------
def scenario_identified(p, name, viewport, mobile):
    print(f"\n--- [{name}] Parcours IDENTIFIÉ : connexion -> 4 étapes -> enregistrement")
    browser, page = new_page(p, viewport, mobile)
    page.goto(BASE + "/"); page.wait_for_load_state("networkidle")
    check("Accueil : bouton « Signaler un accident » très visible (grand)", page.locator(".hero a.btn-accent").bounding_box()["height"] >= 52)
    page.click(".hero a.btn-accent")
    login_via_otp(page)
    page.wait_for_load_state("networkidle")
    check("Après connexion : retour sur le formulaire, étape 1", page.url.split("#")[0].endswith("/report/") and step(page) == 1)

    # ---- Étape 1
    check("Étape 1 : titre « Que s'est-il passé ? »", "Que s'est-il passé ?" in page.locator(".report-step.is-active h2").inner_text())
    check("Étape 1 : 8 cartes de types visuelles avec icônes SVG", page.locator(".type-card").count() == 8 and page.locator(".type-icon svg").count() == 8)
    labels = [t.strip() for t in page.locator(".type-name").all_inner_texts()]
    check("Étape 1 : types = nature de l'accident, sans véhicule", labels == ["Collision", "Renversement", "Sortie de voie", "Perte de contrôle", "Accident à une intersection", "Carambolage / multi-collision", "Accident impliquant un piéton", "Autre"], str(labels))
    check("Étape 1 : mode identifié, numéro masqué", re.search(r"\+228\*{4}\d{2}", page.locator("#mode-note").inner_text()) is not None)
    check("Indicateur : étape 1 active, progression 25 %", page.locator(".stepper-item.is-active").get_attribute("data-step") == "1" and page.get_attribute("#wizard-progress", "aria-valuenow") == "1")
    check("Étapes futures non cliquables", page.locator(".stepper-item[data-step='3'] .stepper-btn").is_disabled())
    check("« Continuer » désactivé tant qu'aucun type n'est choisi", page.locator("#step1-next").is_disabled())
    no_overflow(page, "Étape 1"); tap_targets_ok(page, "Étape 1"); no_emoji(page, "Étape 1")
    if mobile:
        sticky_bar_visible(page, viewport, "#step1-next", "Étape 1")
    page.screenshot(path=f"{SHOTS}/{name}_flow_1_type.png")
    pick_type(page, mobile, "INTERSECTION")
    check("Un toucher sur une carte avance directement à l'étape 2", step(page) == 2)
    check("Indicateur : étape 1 terminée", page.locator(".stepper-item[data-step='1']").evaluate("e => e.classList.contains('is-done')"))

    # ---- Étape 2 : GPS automatique
    page.wait_for_function("document.getElementById('id_latitude').value !== ''")
    lat, lon = float(page.input_value("#id_latitude")), float(page.input_value("#id_longitude"))
    check("Étape 2 : position récupérée automatiquement (GPS autorisé)", abs(lat - LOME["latitude"]) < 1e-4 and abs(lon - LOME["longitude"]) < 1e-4, f"{lat},{lon}")
    check("Étape 2 : carte Leaflet et repère affichés", page.locator("#location-map .leaflet-pane").count() > 0 and page.locator(".leaflet-marker-icon").count() == 1)
    check("Étape 2 : « L'accident s'est-il produit ici ? »", "L'accident s'est-il produit ici ?" in page.locator("#location-question").inner_text())
    page.wait_for_function(f"document.getElementById('place-label').textContent.includes('{PLACE}')")
    check("Étape 2 : indication lisible du lieu affichée", PLACE in page.locator("#place-label").inner_text())
    check("Étape 2 : latitude et longitude visibles avant confirmation", page.locator("#id_latitude").is_visible() and page.locator("#id_longitude").is_visible())
    check("Étape 2 : message de succès GPS", "Position trouvée" in page.locator("#location-status").inner_text())
    check("Étape 2 : bouton « Confirmer cette position » actif", page.locator("#confirm-position-btn").is_enabled() and "Confirmer cette position" in page.locator("#confirm-position-btn").inner_text())
    no_overflow(page, "Étape 2"); tap_targets_ok(page, "Étape 2")
    if mobile:
        sticky_bar_visible(page, viewport, "#confirm-position-btn", "Étape 2")
        for selector, what in (("#location-question", "la question « L'accident s'est-il produit ici ? »"), ("#place-label", "le quartier"),
                               ("#id_latitude", "la latitude"), ("#id_longitude", "la longitude")):
            box = page.locator(selector).bounding_box()
            check(f"Étape 2 (téléphone) : {what} visible sans défiler", box and box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"], str(box))
        map_box = page.locator("#location-map").bounding_box()
        check("Étape 2 (téléphone) : la carte reste assez grande (>= 200 px visibles)", map_box["height"] >= 200, str(map_box))
    page.screenshot(path=f"{SHOTS}/{name}_flow_2_location.png")
    # Corriger la position en déplaçant le repère (clic carte) puis revenir à la position GPS
    click_map(page, mobile, 60, 40)
    moved = float(page.input_value("#id_latitude"))
    check("Étape 2 : la position peut être corrigée sur la carte", abs(moved - lat) > 1e-5)
    page.click("#gps-btn"); page.wait_for_function(f"Math.abs(parseFloat(document.getElementById('id_latitude').value) - {LOME['latitude']}) < 1e-4")
    page.click("#confirm-position-btn"); wait_step(page, 3)
    check("Confirmation de la position -> étape 3", step(page) == 3)

    # ---- Étape 3
    tmp = tempfile.mkdtemp()
    photo = make_photo(tmp)
    check("Étape 3 : date et heure préremplies", re.fullmatch(r"\d{4}-\d{2}-\d{2}", page.input_value("#id_accident_date")) and re.fullmatch(r"\d{2}:\d{2}", page.input_value("#id_accident_time")))
    check("Étape 3 : véhicules FACULTATIF, vide par défaut, étiquette « facultatif »", page.input_value("#id_vehicle_count") == "" and "facultatif" in page.locator("#vehicle-counter").inner_text() and "Nombre approximatif de véhicules impliqués" in page.locator("#vehicle-counter").inner_text())
    check("Véhicules : clavier numérique sur mobile", page.get_attribute("#id_vehicle_count", "inputmode") == "numeric")
    bump(page, "id_vehicle_count", 1, -1)
    check("Véhicules : « − » sur un champ vide ne crée pas de valeur", page.input_value("#id_vehicle_count") == "")
    fill_details(page, photo)
    check("Étape 3 : compteurs +/- (véhicules 2, blessés 3, décès 1)", (page.input_value("#id_vehicle_count"), page.input_value("#id_injured_count"), page.input_value("#id_death_count")) == ("2", "3", "1"))
    bump(page, "id_death_count", 2, -1)
    check("Étape 3 : un compteur ne descend jamais sous zéro", page.input_value("#id_death_count") == "0")
    bump(page, "id_death_count", 1)
    check("Étape 3 : aperçu de la photo + bouton « Changer »", page.locator("#photo-preview").is_visible() and "Changer" in page.locator("#photo-btn").inner_text())
    no_overflow(page, "Étape 3"); tap_targets_ok(page, "Étape 3"); no_emoji(page, "Étape 3")
    page.screenshot(path=f"{SHOTS}/{name}_flow_3_details.png", full_page=True)

    # ---- Étape 4 : récapitulatif
    go_to_recap(page, mobile)
    recap = page.locator("#recap").inner_text()
    check("Récapitulatif : type d'accident", "Accident à une intersection" in page.locator("#recap-type").inner_text())
    check("Récapitulatif : lieu lisible + coordonnées", PLACE in page.locator("#recap-place").inner_text() and re.search(r"6\.1319\d*, 1\.2228\d*", page.locator("#recap-coords").inner_text()), page.locator("#recap-coords").inner_text())
    check("Récapitulatif : date, heure, gravité, nombres", re.search(r"\d{2}/\d{2}/\d{4} à \d{2}:\d{2}", page.locator("#recap-datetime").inner_text()) and "Grave" in page.locator("#recap-severity").inner_text() and "Véhicules : 2" in page.locator("#recap-counts").inner_text() and "Blessés : 3" in page.locator("#recap-counts").inner_text() and "Décès : 1" in page.locator("#recap-counts").inner_text())
    check("Récapitulatif : description et photo", "Collision au carrefour" in page.locator("#recap-description").inner_text() and page.locator("#recap-photo").is_visible())
    check("Récapitulatif : confidentialité (identifié)", "rattaché à votre compte" in page.locator("#recap-declarant").inner_text().lower())
    check("Récapitulatif : icône SVG du type", page.locator("#recap-type-icon svg").count() == 1)
    check("Bouton « Envoyer le signalement » clair", page.locator("#submit-report").inner_text().strip() == "Envoyer le signalement")
    no_overflow(page, "Étape 4"); tap_targets_ok(page, "Étape 4"); no_emoji(page, "Étape 4")
    if mobile:
        sticky_bar_visible(page, viewport, "#submit-report", "Étape 4")
    page.screenshot(path=f"{SHOTS}/{name}_flow_4_recap.png", full_page=True)

    # Navigation : « Modifier » puis retour du navigateur
    page.click(".recap-edit[data-goto='1']"); wait_step(page, 1)
    check("« Modifier » ramène à l'étape du type", step(page) == 1 and page.locator("#type-INTERSECTION").is_checked())
    page.go_back(); wait_step(page, 4)
    check("Bouton « Retour » du navigateur : revient à l'étape précédente", step(page) == 4)
    page.go_back(); wait_step(page, 3); page.go_back(); wait_step(page, 2)
    check("Retour du navigateur : remonte les étapes (4 -> 3 -> 2)", step(page) == 2)
    page.click("#confirm-position-btn"); wait_step(page, 3); go_to_recap(page, mobile)

    page.click("#submit-report")
    page.wait_for_url(re.compile(r"/report/success/"))
    check("Confirmation : « Votre signalement a été enregistré. »", "Votre signalement a été enregistré." in page.locator("#success-message").inner_text())
    check("Confirmation : « en attente de vérification »", "en attente de vérification" in page.locator("main").inner_text())
    page.screenshot(path=f"{SHOTS}/{name}_flow_5_success.png")
    report = record(page)
    check("BASE : rattaché à l'utilisateur, is_anonymous = False, statut En attente", report.user is not None and report.is_anonymous is False and report.status == "PENDING")
    check("BASE : type, gravité et nombres choisis", (report.accident_type, report.severity, report.vehicle_count, report.injured_count, report.death_count) == ("INTERSECTION", "SEVERE", 2, 3, 1))
    check("BASE : coordonnées = position confirmée (PointField SRID 4326)", abs(report.location.y - lat) < 1e-4 and abs(report.location.x - lon) < 1e-4 and report.location.srid == 4326 and abs(report.latitude - report.location.y) < 1e-6)
    data = Path(report.photo.path).read_bytes()
    check("BASE : photo sans EXIF/GPS/appareil/auteur", report.photo and all(m not in data for m in (b"Exif", b"Apple", b"iPhone", b"Dupont", b"GPS")))
    check("BASE : nom de fichier aléatoire", "Dupont" not in report.photo.name and re.search(r"[0-9a-f]{32}\.jpg$", report.photo.name))
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    check("Aucun fichier statique manquant", not page.bad, str(page.bad))
    browser.close()


def scenario_anonymous_gps_denied(p, name, viewport, mobile):
    print(f"\n--- [{name}] Parcours ANONYME : GPS refusé -> sélection manuelle")
    browser, page = new_page(p, viewport, mobile, geolocation=False)
    page.goto(BASE + "/"); page.wait_for_load_state("networkidle")
    page.click(".hero a.btn-outline-light:has-text('anonymement')")
    page.wait_for_url(re.compile(r"/anonymous-report/")); page.wait_for_load_state("networkidle")
    check("Anonyme : accessible sans connexion, mode affiché", "Signalement anonyme" in page.locator("#mode-note").inner_text() and "Anonyme" in page.locator("#mode-chip").inner_text())
    check("Anonyme : aucun numéro affiché", "+228" not in page.locator("main").inner_text())
    pick_type(page, mobile, "PEDESTRIAN")
    page.wait_for_selector("#location-status.alert-warning")
    check("GPS refusé : « Veuillez autoriser l'accès à votre position »", "Veuillez autoriser l'accès à votre position" in page.locator("#location-status").inner_text())
    check("GPS refusé : invitation à toucher la carte", "Touchez la carte" in page.locator("#location-lead").inner_text())
    check("GPS refusé : « Confirmer » reste désactivé sans position", page.locator("#confirm-position-btn").is_disabled())
    check("GPS refusé : bouton « Utiliser ma position actuelle » de nouveau actif", page.locator("#gps-btn").is_enabled())
    page.screenshot(path=f"{SHOTS}/{name}_flow_gps_denied.png")
    click_map(page, mobile)
    lat, lon = float(page.input_value("#id_latitude")), float(page.input_value("#id_longitude"))
    check("Sélection manuelle : latitude/longitude affichées avant confirmation", 5.5 < lat < 6.8 and 0.7 < lon < 1.8, f"{lat},{lon}")
    check("Sélection manuelle : repère affiché et bouton actif", page.locator(".leaflet-marker-icon").count() == 1 and page.locator("#confirm-position-btn").is_enabled())
    check("Position dans la zone : aucun avertissement", page.locator("#outside-zone-box").is_hidden())
    page.click("#confirm-position-btn"); wait_step(page, 3)
    fill_details(page, vehicles=0)                # véhicules laissés vides
    go_to_recap(page, mobile)
    check("Récapitulatif : véhicules « non renseigné » quand le champ est vide", "Véhicules : non renseigné" in page.locator("#recap-counts").inner_text())
    check("Récapitulatif anonyme : aucune identité", "aucune identité n'est enregistrée" in page.locator("#recap-declarant").inner_text())
    check("Récapitulatif : photo absente", page.locator("#recap-photo-row").is_hidden())
    page.click("#submit-report"); page.wait_for_url(re.compile(r"/report/success/"))
    check("Confirmation anonyme : « enregistré anonymement »", "Votre signalement a été enregistré anonymement." in page.locator("#success-message").inner_text())
    report = record(page)
    check("BASE : is_anonymous = True et aucun utilisateur", report.is_anonymous is True and report.user is None)
    check("BASE : type et coordonnées = ceux affichés (manuel)", report.accident_type == "PEDESTRIAN" and abs(report.latitude - lat) < 1e-6 and abs(report.longitude - lon) < 1e-6)
    check("BASE : sans photo", not report.photo)
    check("BASE : véhicules non renseignés = NULL (et non 0 ni 1)", report.vehicle_count is None)
    check("Aucune erreur JavaScript", not page.errors, str(page.errors))
    browser.close()


def scenario_gps_failures(p, name, viewport, mobile):
    print(f"\n--- [{name}] GPS : indisponible / introuvable / délai / peu précis")
    cases = [
        ("navigateur sans géolocalisation", "delete Navigator.prototype.geolocation;", True, "ne permet pas la géolocalisation"),
        ("position introuvable", "navigator.geolocation.getCurrentPosition = (ok, err) => setTimeout(() => err({code: 2}), 50);", True, "indisponible"),
        ("délai dépassé", "navigator.geolocation.getCurrentPosition = (ok, err) => setTimeout(() => err({code: 3}), 50);", True, "trop de temps"),
    ]
    for label, script, geoloc, expected in cases:
        browser, page = new_page(p, viewport, mobile, geolocation=geoloc, init_script=script)
        page.goto(BASE + "/anonymous-report/"); page.wait_for_load_state("networkidle")
        pick_type(page, mobile)
        page.wait_for_selector("#location-status.alert-warning")
        check(f"GPS {label} : message clair", expected in page.locator("#location-status").inner_text())
        click_map(page, mobile)
        check(f"GPS {label} : la sélection manuelle reste possible", page.locator("#confirm-position-btn").is_enabled())
        browser.close()
    browser, page = new_page(p, viewport, mobile, accuracy=600)
    page.goto(BASE + "/anonymous-report/"); page.wait_for_load_state("networkidle")
    pick_type(page, mobile)
    page.wait_for_selector("#location-status.alert-warning")
    check("GPS peu précis : avertissement de précision, position tout de même proposée", "peu précise" in page.locator("#location-status").inner_text() and page.input_value("#id_latitude") != "")
    browser.close()


def scenario_outside_zone(p, name, viewport, mobile):
    print(f"\n--- [{name}] Position HORS ZONE : avertissement, confirmation, coordonnées conservées")
    browser, page = new_page(p, viewport, mobile, geolocation=False)
    page.goto(BASE + "/anonymous-report/"); page.wait_for_load_state("networkidle")
    pick_type(page, mobile, "RUN_OFF_ROAD")
    for _ in range(5):
        page.click(".leaflet-control-zoom-out"); page.wait_for_timeout(450)
    box = page.locator("#location-map").bounding_box()
    tap_or_click(page, mobile, "#location-map", {"x": box["width"] - 70, "y": box["height"] - 70})
    page.wait_for_selector("#outside-zone-box:not([hidden])")
    lat, lon = page.input_value("#id_latitude"), page.input_value("#id_longitude")
    text = page.locator("#outside-zone-box").inner_text()
    check("Avertissement : « en dehors de la zone couverte par ACCIMAP »", "semble être en dehors de la zone couverte par ACCIMAP" in text)
    check("Zone présentée comme indicative / non officielle", "indicative" in text and "pas une limite administrative officielle" in text)
    check("Le bouton devient « Confirmer malgré tout »", "Confirmer malgré tout" in page.locator("#confirm-position-btn").inner_text())
    check("La position n'est PAS déplacée vers la zone", float(lat) < 6.08)
    page.screenshot(path=f"{SHOTS}/{name}_flow_outside.png")
    page.click("#confirm-position-btn"); wait_step(page, 3)
    fill_details(page); go_to_recap(page, mobile)
    page.click("#submit-report"); page.wait_for_url(re.compile(r"/report/success/"))
    report = record(page)
    check("Hors zone confirmée : signalement enregistré (jamais rejeté)", report.pk is not None)
    check("BASE : coordonnées EXACTEMENT celles choisies", f"{report.latitude:.6f}" == lat and f"{report.longitude:.6f}" == lon, f"{report.latitude},{report.longitude} vs {lat},{lon}")
    browser.close()


def scenario_validation(p, name, viewport, mobile):
    print(f"\n--- [{name}] Validation : client puis SERVEUR")
    browser, page = new_page(p, viewport, mobile)
    page.goto(BASE + "/anonymous-report/"); page.wait_for_load_state("networkidle")
    page.click("#step1-next", force=True) if page.locator("#step1-next").is_enabled() else None
    check("Étape 1 : impossible de continuer sans type", step(page) == 1 and page.locator("#step1-next").is_disabled())
    page.locator("#stepper .stepper-item[data-step='4'] .stepper-btn").click(force=True)
    check("Étape 4 non atteignable depuis l'indicateur sans passer par les étapes", step(page) == 1)
    page.focus("#type-COLLISION"); page.keyboard.press("Space")
    check("Clavier : choisir un type n'avance pas tout seul (bouton « Continuer »)", step(page) == 1 and page.locator("#step1-next").is_enabled())
    page.press("#type-COLLISION", "Enter")
    wait_step(page, 2)
    page.wait_for_function("document.getElementById('id_latitude').value !== ''")
    page.click("#confirm-position-btn"); wait_step(page, 3)

    page.fill("#id_accident_date", "")
    page.click("#step3-next")
    page.wait_for_timeout(300)
    check("Étape 3 : date obligatoire, on reste sur l'étape", step(page) == 3)
    page.evaluate("""() => { const d = new Date(); d.setDate(d.getDate() + 5);
        const f = document.getElementById('id_accident_date'); f.removeAttribute('max'); f.value = d.toISOString().slice(0, 10); }""")
    tmp = tempfile.mkdtemp()
    page.fill("#id_description", "Texte à conserver après erreur")
    page.set_input_files("#id_photo", make_photo(tmp))
    page.click("#step3-next"); wait_step(page, 4)
    page.click("#submit-report")
    page.wait_for_load_state("networkidle")
    check("Erreur SERVEUR (date future) : la page est réaffichée, aucun signalement créé", "/report/success/" not in page.url and AccidentReport.objects.filter(description="Texte à conserver après erreur").count() == 0)
    wait_step(page, 3)
    check("Erreur SERVEUR : l'étape à corriger s'ouvre automatiquement (étape 3)", step(page) == 3 and page.locator(".invalid-feedback").count() >= 1)
    check("Erreur SERVEUR : message explicite (date/heure dans le futur)", "futur" in page.locator("main").inner_text())
    check("Erreur SERVEUR : la saisie est conservée (type, position, description)",
          page.locator("#type-COLLISION").is_checked() and page.input_value("#id_latitude") != "" and page.input_value("#id_description") == "Texte à conserver après erreur")
    check("Erreur SERVEUR : on prévient que la photo doit être rechoisie", "ne conserve pas la photo" in page.locator("main").inner_text())
    page.screenshot(path=f"{SHOTS}/{name}_flow_server_error.png", full_page=True)
    check("Aucune erreur JavaScript", not [e for e in page.errors if "400" not in e], str(page.errors))
    browser.close()


def scenario_no_javascript(p, name, viewport, mobile):
    print(f"\n--- [{name}] Repli SANS JavaScript")
    browser, page = new_page(p, viewport, mobile, geolocation=False, js=False)
    page.goto(BASE + "/anonymous-report/"); page.wait_for_load_state("domcontentloaded")
    check("Sans JS : les 4 étapes sont toutes visibles", all(page.locator(f".report-step[data-step='{n}']").is_visible() for n in (1, 2, 3, 4)))
    check("Sans JS : champs latitude/longitude modifiables", page.get_attribute("#id_latitude", "readonly") is None)
    page.click("label[for='type-OTHER']")
    page.fill("#id_latitude", "6.1500"); page.fill("#id_longitude", "1.2300")
    page.fill("#id_description", "Envoi sans JavaScript")
    page.click("#submit-report", force=True); page.wait_for_url(re.compile(r"/report/success/"))   # page longue : défilement fluide
    report = record(page)
    check("Sans JS : le signalement s'enregistre (validation serveur)", report.accident_type == "OTHER" and report.is_anonymous and abs(report.latitude - 6.15) < 1e-6)
    browser.close()


def scenario_tiles_unavailable(p, name, viewport, mobile):
    print(f"\n--- [{name}] Fond de carte INACCESSIBLE et quartier indisponible")
    browser, page = new_page(p, viewport, mobile, geolocation=False, place=None)
    page.context.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(status=403))
    page.goto(BASE + "/anonymous-report/"); page.wait_for_load_state("networkidle")
    pick_type(page, mobile)
    page.wait_for_selector("#location-status.alert-warning")
    check("Tuiles bloquées : l'utilisateur est prévenu", page.locator("#location-status").inner_text() != "")
    click_map(page, mobile)
    check("Tuiles bloquées : on peut quand même placer le repère", page.locator(".leaflet-marker-icon").count() == 1 and page.locator("#confirm-position-btn").is_enabled())
    page.wait_for_function("document.getElementById('place-label').textContent.includes('non disponible')")
    check("Quartier indisponible : message neutre, parcours non bloqué", "quartier non disponible" in page.locator("#place-label").inner_text())
    page.click("#confirm-position-btn"); wait_step(page, 3); fill_details(page); go_to_recap(page, mobile)
    check("Récapitulatif : repli « Position choisie sur la carte »", "Position choisie sur la carte" in page.locator("#recap-place").inner_text())
    browser.close()


def cleanup():
    reports = AccidentReport.objects.filter(reference__in=created_refs)
    for report in reports:
        if report.photo:
            Path(report.photo.path).unlink(missing_ok=True)
    n = reports.delete()[0]
    OTPCode.objects.filter(phone_number__in=created_phones).delete()
    User.objects.filter(phone_number__in=created_phones).delete()
    print(f"\nNettoyage : {n} signalement(s) d'essai, {len(created_phones)} numéro(s) supprimés.")


if __name__ == "__main__":
    which = os.environ.get("E2E_VIEWPORT", "both")
    suites = [("mobile", {"width": 390, "height": 844}, True), ("desktop", {"width": 1366, "height": 800}, False)]
    suites = [s for s in suites if which in ("both", s[0])]
    try:
        with sync_playwright() as p:
            for name, viewport, mobile in suites:
                print(f"\n================ {name.upper()} {viewport['width']}x{viewport['height']} ================")
                for scenario in (scenario_identified, scenario_anonymous_gps_denied, scenario_gps_failures, scenario_outside_zone,
                                 scenario_validation, scenario_no_javascript, scenario_tiles_unavailable):
                    try:
                        scenario(p, name, viewport, mobile)
                    except Exception:
                        check(f"{scenario.__name__} s'est exécuté sans exception", False, traceback.format_exc(limit=3))
    finally:
        cleanup()
    ok = sum(results)
    print(f"\nRÉSULTAT : {ok}/{len(results)} contrôles réussis")
    sys.exit(0 if ok == len(results) else 1)
