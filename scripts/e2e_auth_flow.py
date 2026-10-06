"""
Vérification de bout en bout du parcours d'authentification dans un vrai navigateur
(Chromium via Playwright), en format smartphone puis ordinateur.

Prérequis (hors requirements.txt, usage développeur) :
    pip install playwright && playwright install chromium

Utilisation (serveur lancé en mode développement, OTP_DEV_MODE=True) :
    python manage.py runserver 8000
    python scripts/e2e_auth_flow.py

Variables facultatives : ACCIMAP_URL (défaut http://localhost:8000),
E2E_SHOTS (dossier des captures d'écran, défaut e2e_screenshots).
Chaque exécution utilise des numéros de téléphone aléatoires (le délai de renvoi
de 60 s empêcherait de réutiliser le même numéro).
"""
import random, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

import os
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
import django  # noqa: E402

django.setup()
from accounts.models import OTPCode, User  # noqa: E402  (nettoyage des comptes de test)

created_phones = []
BASE = os.environ.get("ACCIMAP_URL", "http://localhost:8000")
SHOTS = os.environ.get("E2E_SHOTS", "e2e_screenshots")
os.makedirs(SHOTS, exist_ok=True)
results = []

def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))

def run(p, name, viewport, phone, mobile):
    print(f"\n=== {name.upper()} {viewport['width']}x{viewport['height']} ===")
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport=viewport, has_touch=mobile, is_mobile=mobile,
                              device_scale_factor=2 if mobile else 1, locale="fr-FR")
    # Les tuiles OpenStreetMap sont simulées : le test ne dépend pas d'un accès réseau.
    ctx.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(
        status=200, content_type="image/png",
        body=bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da6364f8ffff3f0005fe02fea7d9a5ee0000000049454e44ae426082")))
    page = ctx.new_page()
    page.set_default_timeout(8000)
    errors, bad = [], []
    def on_console(m):
        if m.type != "error":
            return
        if "403" in m.text and m.location.get("url", "").endswith("/dashboard/"):
            return  # attendu : un citoyen n'a pas accès à /dashboard/
        errors.append(f"{m.text} @ {m.location.get('url')}")
    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 and "/static/" in r.url else None)

    def no_overflow(label):
        w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        check(f"{label} : pas de défilement horizontal", w[0] <= w[1], f"scrollWidth={w[0]} > {w[1]}")

    def open_menu():
        if mobile:
            page.click(".navbar-toggler"); page.wait_for_selector("#mainNav.show")

    # 1. Accueil
    page.goto(BASE + "/"); page.wait_for_load_state("networkidle")
    check("Accueil : marque ACCIMAP et titre d'accroche visibles", page.locator(".hero-brand", has_text="ACCIMAP").is_visible() and page.locator("h1", has_text="Signalez-le").is_visible())
    for label in ["Signaler un accident", "Signaler anonymement"]:
        check(f"Accueil : bouton « {label} » visible", page.locator(".hero a.btn", has_text=label).is_visible())
    check("Accueil : lien « Se connecter » visible", page.locator(".hero a", has_text="Se connecter").is_visible())
    no_overflow("Accueil")
    check("Menu hamburger " + ("visible (mobile)" if mobile else "masqué (ordinateur)"),
          page.locator(".navbar-toggler").is_visible() == mobile)
    if not mobile:
        check("Navbar ordinateur : liens visibles sans hamburger", page.locator("#mainNav a.nav-link", has_text="Carte").is_visible())
    heights = page.evaluate("[...document.querySelectorAll('main .btn')].filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().height))")
    check("Cibles tactiles >= 44 px (tous les boutons de l'accueil)", heights and all(h >= 44 for h in heights), str(heights))
    page.screenshot(path=f"{SHOTS}/{name}_1_home.png")
    if mobile:
        open_menu(); page.screenshot(path=f"{SHOTS}/{name}_1b_menu.png")
        check("Menu mobile : « Se connecter » accessible", page.locator("#mainNav a", has_text="Se connecter").is_visible())
        page.click(".navbar-toggler"); page.wait_for_selector("#mainNav:not(.show)")

    # 2. Accès protégé -> connexion avec retour
    page.click(".hero a.btn >> text=Signaler un accident")
    page.wait_for_url(re.compile(r"/login/\?next=/report/"))
    check("« Signaler un accident » sans connexion -> /login/?next=/report/", True)

    # 3. Numéro
    no_overflow("Connexion")
    page.screenshot(path=f"{SHOTS}/{name}_2_login.png")
    page.fill("#id_phone_number", "abc"); page.click("button[type=submit]")
    page.wait_for_selector(".invalid-feedback")
    check("Numéro invalide : message d'erreur affiché", "invalide" in page.locator(".invalid-feedback").inner_text())
    page.fill("#id_phone_number", phone); page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"/verify-otp/"))
    check("Numéro valide -> page /verify-otp/", True)

    # 4. OTP (mode test)
    banner = page.locator("#dev-banner")
    check("Bandeau « Mode développement — SMS simulé » visible",
          banner.is_visible() and "MODE DÉVELOPPEMENT" in banner.inner_text() and "SMS simulé" in banner.inner_text())
    code = page.locator("#dev-code").inner_text().strip()
    check("Code OTP affiché (6 chiffres)", re.fullmatch(r"\d{6}", code), code)
    html = page.content()
    digits = re.sub(r"\D", "", phone)
    check("Numéro affiché masqué (jamais en entier)", "+228****" in html and ("+228" + digits) not in html)
    otp_font = page.evaluate("getComputedStyle(document.querySelector('.otp-input')).fontSize")
    check("Champ OTP : police >= 16 px (évite le zoom iOS)", float(otp_font[:-2]) >= 16, otp_font)
    check("Champ OTP : clavier numérique + autofill SMS",
          page.get_attribute(".otp-input", "inputmode") == "numeric" and page.get_attribute(".otp-input", "autocomplete") == "one-time-code")
    resend = page.locator("#resend-btn")
    check("Bouton « Renvoyer » désactivé avec compte à rebours",
          resend.is_disabled() and re.search(r"\(\d+ s\)", resend.inner_text()), resend.inner_text())
    heights = page.evaluate("[...document.querySelectorAll('main .btn')].filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().height))")
    check("Cibles tactiles >= 44 px (tous les boutons de la page OTP)", heights and all(h >= 44 for h in heights), str(heights))
    check("Compte à rebours : espace avant « (N s) »", page.evaluate("getComputedStyle(document.getElementById('resend-btn')).columnGap") not in ("normal", "0px"))
    no_overflow("Vérification OTP")
    page.screenshot(path=f"{SHOTS}/{name}_3_verify.png")

    page.fill(".otp-input", ""); page.type(".otp-input", "ab12cd")
    check("Champ OTP : lettres filtrées automatiquement", page.input_value(".otp-input") == "12", page.input_value(".otp-input"))
    wrong = "000000" if code != "000000" else "111111"
    page.fill(".otp-input", wrong); page.click("button[type=submit]")
    page.wait_for_selector(".alert-danger")
    check("Code incorrect : « Le code OTP est incorrect. »", "Le code OTP est incorrect." in page.locator(".alert-danger").inner_text())
    page.screenshot(path=f"{SHOTS}/{name}_3b_wrong.png")

    page.fill(".otp-input", code); page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"/report/"))
    check("Code correct -> retour à la page demandée (/report/)", True)
    check("Message « Connexion réussie »", "Connexion réussie" in page.locator(".alert-success").inner_text())

    # 5. Connecté
    page.goto(BASE + "/"); page.wait_for_load_state("networkidle")
    open_menu()
    check("Connecté : numéro masqué dans la navbar", re.search(r"\+228\*{4}\d{2}", page.locator(".navbar").inner_text()) is not None)
    check("Connecté : bouton « Se déconnecter » présent", page.locator(".navbar button", has_text="Se déconnecter").is_visible())
    check("Connecté : plus de « Se connecter » dans la navbar", page.locator(".navbar a", has_text="Se connecter").count() == 0)
    check("Connecté : pas de lien Tableau de bord (non admin)", page.locator(".navbar a", has_text="Tableau de bord").count() == 0)
    page.screenshot(path=f"{SHOTS}/{name}_4_logged.png")
    r = page.goto(BASE + "/dashboard/")
    check("Citoyen -> /dashboard/ : accès refusé (403)", r.status == 403)

    # 6. Déconnexion
    page.goto(BASE + "/"); open_menu()
    page.click(".navbar button >> text=Se déconnecter")
    page.wait_for_selector(".alert-success")
    check("Déconnexion : message affiché", "déconnecté" in page.locator(".alert-success").inner_text())
    check("Déconnecté : « Se connecter » de retour sur l'accueil", page.locator(".hero a", has_text="Se connecter").is_visible())
    page.goto(BASE + "/report/")
    check("Après déconnexion : /report/ redemande la connexion", "/login/" in page.url)

    # 7. Pages annexes
    for path in ["/about/", "/map/", "/anonymous-report/"]:
        page.goto(BASE + path); page.wait_for_load_state("networkidle")
        no_overflow(path)
    check("Aucune erreur JavaScript", not errors, str(errors))
    check("Aucun fichier statique manquant (4xx/5xx)", not bad, str(bad))
    browser.close()

try:
    with sync_playwright() as p:
        def fresh():  # numéro neuf à chaque exécution (évite le délai de renvoi de 60 s)
            n = "9" + "".join(random.choice("0123456789") for _ in range(7))
            created_phones.append("+228" + n)
            return f"{n[:2]} {n[2:4]} {n[4:6]} {n[6:]}"
        run(p, "mobile", {"width": 390, "height": 844}, fresh(), True)
        run(p, "desktop", {"width": 1366, "height": 800}, fresh(), False)
finally:
    # Les comptes citoyens et codes créés par ce test sont supprimés de la base de développement.
    User.objects.filter(phone_number__in=created_phones).delete()
    OTPCode.objects.filter(phone_number__in=created_phones).delete()

ok = sum(results)
print(f"\nRÉSULTAT : {ok}/{len(results)} contrôles réussis")
sys.exit(0 if ok == len(results) else 1)
