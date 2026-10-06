"""
Vérification RÉELLE des services externes d'ACCIMAP (phase 9) : aucune simulation réseau.

  1. Nominatim (géocodage inverse, appelé par le SERVEUR) : 2 appels réels au total, espacés de plus d'une seconde,
     identifiés par le User-Agent d'ACCIMAP (politique d'usage : https://operations.osmfoundation.org/policies/nominatim/).
     + comportement si le service est injoignable (adresse locale fermée : aucun appel externe).
  2. Tuiles OpenStreetMap (chargées par le NAVIGATEUR) : carte publique et étape « Lieu » du signalement, zoom,
     déplacement, puis coupure réseau des tuiles (message d'avertissement attendu, carte toujours utilisable).

Usage modéré : à lancer ponctuellement (avant/après un déploiement), jamais en boucle.

Prérequis : pip install -r requirements-dev.txt && playwright install chromium
Utilisation : python manage.py runserver 8000   puis   python scripts/check_external_services.py
              ACCIMAP_URL=https://mon-service.onrender.com python scripts/check_external_services.py   (après déploiement)
Variables : SKIP_NOMINATIM=1 (ne pas appeler Nominatim), E2E_SHOTS (dossier des captures, défaut e2e_screenshots).
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.test import override_settings  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from reports import geocoding  # noqa: E402

BASE = os.environ.get("ACCIMAP_URL", "http://localhost:8000")
SHOTS = os.environ.get("E2E_SHOTS", "e2e_screenshots")
os.makedirs(SHOTS, exist_ok=True)
TILE_HOST = re.compile(r"tile\.openstreetmap\.org/(\d+)/(\d+)/(\d+)\.png")
# Bè, Lomé (quartier connu, dans la zone indicative)
LOME = (6.1385, 1.2265)
results = []
# Historique des messages de la zone d'état de l'étape « Lieu » (un message peut en remplacer un autre).
STATUS_HISTORY = """window.__statusHistory = []; document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('location-status');
  if (el) new MutationObserver(() => window.__statusHistory.push(el.textContent.trim()))
    .observe(el, {childList: true, characterData: true, subtree: true}); });"""


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    return bool(cond)


# ------------------------------------------------------------------------------------------------ Nominatim
def nominatim():
    print("\n=== Nominatim (géocodage inverse, côté serveur) ===")
    print(f"  GEOCODER_URL = {settings.GEOCODER_URL}")
    print(f"  REVERSE_GEOCODING_ENABLED = {settings.REVERSE_GEOCODING_ENABLED}")
    contact = settings.GEOCODER_CONTACT
    check("GEOCODER_CONTACT renseigné (recommandé par la politique d'usage)", bool(contact),
          "vide : à définir dans .env / Render" if not contact else contact)

    if os.environ.get("SKIP_NOMINATIM"):
        print("  (SKIP_NOMINATIM : appels réels ignorés)")
        return

    # Appel 1 : réponse brute, avec EXACTEMENT les en-têtes et paramètres envoyés par reports/geocoding.py
    lat, lon = LOME
    query = urlencode({"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "zoom": 16,
                       "addressdetails": 1, "accept-language": "fr"})
    user_agent = f"ACCIMAP-prototype/1.0{f' ({contact})' if contact else ''}"
    url = f"{settings.GEOCODER_URL}?{query}"
    print(f"  GET {url}\n  User-Agent: {user_agent}")
    started = time.monotonic()
    try:
        with urlopen(Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"}),
                     timeout=geocoding.TIMEOUT_SECONDS) as response:
            status, payload = response.status, json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 (rapport de diagnostic)
        check("Appel réel Nominatim", False, f"{exc.__class__.__name__}: {exc}")
        return
    elapsed = time.monotonic() - started
    address = payload.get("address", {})
    check("HTTP 200 en moins de 3 s (délai maximal du code)", status == 200 and elapsed < geocoding.TIMEOUT_SECONDS,
          f"{status} en {elapsed:.2f} s")
    print(f"  display_name : {payload.get('display_name')}")
    print(f"  address      : {json.dumps(address, ensure_ascii=False)}")
    check("Pays = Togo", address.get("country_code") == "tg", address.get("country"))
    label = geocoding.format_place(payload)
    check("Libellé « Quartier, Ville » construit", bool(label) and "," in label, str(label))
    print(f"  Libellé affiché à l'utilisateur : {label}")

    # Appel 2 : le VRAI chemin applicatif (reverse_geocode, avec limitation 1 appel/s et cache 24 h)
    time.sleep(1.5)
    other = (6.1720, 1.2140)   # Tokoin, Lomé
    first = geocoding.reverse_geocode(*other)
    check("reverse_geocode() réel -> libellé", bool(first), str(first))
    t0 = time.monotonic()
    second = geocoding.reverse_geocode(*other)
    check("2e demande même position : servie par le cache (aucun appel réseau)",
          second == first and time.monotonic() - t0 < 0.05)
    check("Limitation : un 3e appel immédiat (autre position) n'interroge pas le service",
          geocoding.reverse_geocode(6.2000, 1.2500) is None)

    # Service injoignable : adresse locale fermée (aucun trafic externe)
    with override_settings(GEOCODER_URL="http://127.0.0.1:9/reverse"):
        time.sleep(1.1)
        t0 = time.monotonic()
        down = geocoding.reverse_geocode(6.1111, 1.1111)
        check("Service injoignable : None, sans exception, rapidement", down is None and time.monotonic() - t0 < 4,
              f"{time.monotonic() - t0:.2f} s")
    with override_settings(REVERSE_GEOCODING_ENABLED=False):
        check("REVERSE_GEOCODING_ENABLED=False : aucun appel", geocoding.reverse_geocode(6.3, 1.3) is None)


# ------------------------------------------------------------------------------------------------ Tuiles OSM
def track_tiles(page):
    tiles = []
    page.on("response", lambda r: tiles.append((r.status, r.headers.get("content-type", ""), r.url))
            if TILE_HOST.search(r.url) else None)
    page.on("requestfailed", lambda r: tiles.append((0, r.failure or "", r.url)) if TILE_HOST.search(r.url) else None)
    return tiles


def failed(tiles):
    """Vraies erreurs : HTTP >= 400 ou échec réseau. ERR_ABORTED = tuile annulée par Leaflet (zoom/déplacement)."""
    return [t for t in tiles if t[0] >= 400 or (t[0] == 0 and "ERR_ABORTED" not in str(t[1]))]


def zooms(tiles):
    return sorted({int(TILE_HOST.search(u).group(1)) for s, _, u in tiles if s == 200})


def loaded_tiles(page):
    return page.locator(".leaflet-tile-loaded").count()


def osm_public_map(browser, name, viewport, mobile):
    print(f"\n=== Tuiles OpenStreetMap : carte publique [{name}] ===")
    print(f"  MAP_TILE_URL = {settings.MAP_TILE_URL}")
    ctx = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile, locale="fr-FR")
    page = ctx.new_page()
    page.set_default_timeout(20000)
    tiles = track_tiles(page)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/map/")
    page.wait_for_function("document.querySelectorAll('.leaflet-tile-loaded').length >= 4")
    page.wait_for_load_state("networkidle")
    ok = [t for t in tiles if t[0] == 200]
    check("Tuiles réelles chargées (HTTP 200, image/png)", ok and all(t[1].startswith("image/png") for t in ok),
          f"{len(ok)} tuiles 200 / {len(tiles)} requêtes")
    check("Aucune tuile en erreur (4xx/5xx/réseau)", not failed(tiles), str(failed(tiles)[:3]))
    check("Attribution OpenStreetMap visible", "OpenStreetMap" in page.locator(".leaflet-control-attribution").inner_text())
    check("Pas d'avertissement « fond de carte » affiché", "fond de carte n'a pas pu" not in page.inner_text("body"))
    start_zoom = zooms(tiles)
    page.screenshot(path=f"{SHOTS}/{name}_osm_1_initial.png")

    # Zoom avant (bouton +), deux fois
    before = len(tiles)
    for _ in range(2):
        page.click(".leaflet-control-zoom-in")
        page.wait_for_timeout(900)
    page.wait_for_load_state("networkidle")
    new_z = zooms(tiles[before:])
    check("Zoom avant : tuiles des niveaux supérieurs chargées",
          new_z and max(new_z) == max(start_zoom) + 2, f"{start_zoom} -> {new_z}")

    # Déplacement (glisser la carte)
    before = len(tiles)
    box = page.locator("#public-map").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy); page.mouse.down()
    page.mouse.move(cx - 250, cy - 150, steps=12); page.mouse.up()
    page.wait_for_timeout(800); page.wait_for_load_state("networkidle")
    moved = [t for t in tiles[before:] if t[0] == 200]
    check("Déplacement : nouvelles tuiles chargées", len(moved) > 0, f"{len(moved)} nouvelles tuiles")

    # Zoom arrière (bouton −)
    before = len(tiles)
    page.click(".leaflet-control-zoom-out"); page.wait_for_timeout(900); page.wait_for_load_state("networkidle")
    check("Zoom arrière : tuiles chargées", any(t[0] == 200 for t in tiles[before:]))
    aborted = sum(1 for t in tiles if t[0] == 0 and "ERR_ABORTED" in str(t[1]))
    check("Aucune erreur de tuile sur la session (annulations Leaflet exclues)", not failed(tiles),
          str(failed(tiles)[:3]))
    print(f"  Tuiles annulées par Leaflet pendant zoom/déplacement (normal) : {aborted}")
    check("Aucune erreur JavaScript", not errors, "; ".join(errors[:2]))
    page.screenshot(path=f"{SHOTS}/{name}_osm_2_after_zoom_pan.png")
    print(f"  Total : {len(tiles)} requêtes de tuiles, niveaux de zoom {zooms(tiles)}")
    ctx.close()


def osm_network_failure(browser):
    print("\n=== Tuiles OpenStreetMap : coupure réseau simulée côté navigateur ===")
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="fr-FR",
                              geolocation={"latitude": LOME[0], "longitude": LOME[1], "accuracy": 15},
                              permissions=["geolocation"])
    ctx.route(TILE_HOST, lambda route: route.abort("internetdisconnected"))
    page = ctx.new_page()
    page.set_default_timeout(20000)
    page.goto(BASE + "/map/")
    page.wait_for_function("document.getElementById('map-loading').hidden")
    page.wait_for_timeout(1500)
    body = page.inner_text("body")
    check("Carte publique : avertissement « fond de carte n'a pas pu être chargé »", "fond de carte n'a pas pu être chargé" in body)
    check("Carte publique : compteur de signalements toujours affiché", bool(page.locator("#map-count").inner_text().strip()))
    page.screenshot(path=f"{SHOTS}/osm_3_tiles_down_public.png")

    page.add_init_script(STATUS_HISTORY)
    page.goto(BASE + "/anonymous-report/")
    page.locator("label[for='type-COLLISION']").click()
    page.wait_for_selector(".report-step.is-active[data-step='2']")
    page.wait_for_function("document.getElementById('id_latitude').value !== ''")
    page.wait_for_timeout(2500)
    history = page.evaluate("window.__statusHistory || []")
    check("Signalement : avertissement tuiles émis", any("fond de carte" in h for h in history), str(history))
    check("Signalement : position GPS conservée malgré l'absence de fond de carte", page.input_value("#id_latitude") != "")
    if "fond de carte" not in page.locator("#location-status").inner_text():
        print("  LIMITE CONNUE : l'avertissement a été remplacé par le message GPS (même zone d'affichage, ordre aléatoire).")
    check("Signalement : confirmation de position toujours possible", page.locator("#confirm-position-btn").is_enabled())
    page.screenshot(path=f"{SHOTS}/osm_4_tiles_down_report.png")
    ctx.close()


def osm_report_step(browser):
    print("\n=== Tuiles OpenStreetMap + quartier réel : étape « Lieu » du signalement ===")
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="fr-FR",
                              geolocation={"latitude": LOME[0], "longitude": LOME[1], "accuracy": 15},
                              permissions=["geolocation"])
    page = ctx.new_page()
    page.set_default_timeout(20000)
    tiles = track_tiles(page)
    page.goto(BASE + "/anonymous-report/")
    page.locator("label[for='type-COLLISION']").tap()
    page.wait_for_selector(".report-step.is-active[data-step='2']")
    page.wait_for_function("document.querySelectorAll('#location-map .leaflet-tile-loaded').length >= 4")
    check("Étape Lieu : tuiles réelles chargées, aucune erreur (annulations Leaflet exclues)",
          any(t[0] == 200 for t in tiles) and not failed(tiles), f"{len(tiles)} requêtes, erreurs : {failed(tiles)[:3]}")
    if os.environ.get("SKIP_NOMINATIM"):
        ctx.close()
        return
    # /report/place/ -> serveur ACCIMAP -> Nominatim (1 appel réel, le navigateur ne contacte jamais Nominatim)
    try:
        page.wait_for_function("(document.getElementById('place-label').textContent || '').trim().length > 0", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    label = page.locator("#place-label").inner_text().strip()
    check("Étape Lieu : quartier réel affiché via le serveur", bool(label), label or "(vide : service lent/indisponible)")
    print(f"  Libellé affiché : {label}")
    page.screenshot(path=f"{SHOTS}/osm_5_report_location_real.png")
    ctx.close()


def main():
    nominatim()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        osm_public_map(browser, "desktop", {"width": 1366, "height": 900}, False)
        osm_public_map(browser, "mobile", {"width": 390, "height": 844}, True)
        osm_report_step(browser)
        osm_network_failure(browser)
        browser.close()
    passed = sum(results)
    print(f"\nRésultat : {passed}/{len(results)} vérifications réussies")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
