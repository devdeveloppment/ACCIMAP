import re
from pathlib import Path

from django.conf import settings
from django.template import Context, Template, TemplateSyntaxError
from django.test import SimpleTestCase

ICON_RE = re.compile(r"""\{%\s*icon\s+["']([a-z0-9-]+)["']""")


class IconTagTests(SimpleTestCase):
    def render(self, source):
        return Template(source).render(Context())

    def test_rendu_svg_integre(self):
        html = self.render('{% icon "map-pin" %}')
        self.assertTrue(html.startswith("<svg"))
        self.assertIn('stroke="currentColor"', html)
        self.assertIn('aria-hidden="true"', html)
        self.assertIn('width="20"', html)

    def test_taille_et_classe(self):
        html = self.render('{% icon "siren" size=28 css="me-2" %}')
        self.assertIn('width="28"', html)
        self.assertIn('class="icon me-2"', html)

    def test_icone_inconnue_ou_nom_invalide(self):
        for source in ['{% icon "n-existe-pas" %}', '{% icon "../../etc/passwd" %}', '{% icon "Map Pin" %}']:
            with self.subTest(source=source), self.assertRaises(TemplateSyntaxError):
                self.render(source)

    def test_toutes_les_icones_citees_dans_les_gabarits_existent(self):
        icon_dir = Path(settings.BASE_DIR) / "static" / "vendor" / "lucide"
        cited = set()
        for template in (Path(settings.BASE_DIR) / "templates").rglob("*.html"):
            cited |= set(ICON_RE.findall(template.read_text(encoding="utf-8")))
        missing = sorted(name for name in cited if not (icon_dir / f"{name}.svg").is_file())
        self.assertEqual(missing, [], f"Icônes manquantes : {missing}")

    def test_licence_embarquee(self):
        self.assertTrue((Path(settings.BASE_DIR) / "static" / "vendor" / "lucide" / "LICENSE").is_file())


EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D]")
OTHER_ICON_FAMILIES = re.compile(r'class="[^"]*\b(?:bi bi-|fa-|fas |far |fab |glyphicon|material-icons|mdi-)')


def _project_ui_files():
    root = Path(settings.BASE_DIR)
    files = list((root / "templates").rglob("*.html"))
    files += [p for p in (root / "static" / "js").glob("*.js")]
    files += [root / "static" / "css" / "accimap.css", root / "config" / "ui_tags.py"]
    return files


class NoEmojiAndSingleIconFamilyTests(SimpleTestCase):
    def test_aucun_emoji_dans_l_interface(self):
        offenders = {}
        for path in _project_ui_files():
            found = set(EMOJI.findall(path.read_text(encoding="utf-8")))
            if found:
                offenders[str(path.relative_to(settings.BASE_DIR))] = "".join(sorted(found))
        self.assertEqual(offenders, {}, "Emojis interdits dans l'interface (utiliser {% icon %})")

    def test_une_seule_famille_d_icones(self):
        offenders = [str(p) for p in _project_ui_files() if OTHER_ICON_FAMILIES.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])
        vendor = Path(settings.BASE_DIR) / "static" / "vendor"
        icon_libraries = {p.name for p in vendor.iterdir() if "icon" in p.name.lower() or p.name in ("lucide", "fontawesome")}
        self.assertEqual(icon_libraries, {"lucide"})

    def test_le_detecteur_d_emoji_fonctionne(self):
        for sample in ("\U0001F697", "\U0001F691", "\u26A0\uFE0F", "\U0001F4CD", "\u2705", "\u2B50"):
            self.assertTrue(EMOJI.search(sample), sample)
        for fine in ("‹ ›", "·", "é", "→", "…", "—", "×"):
            self.assertFalse(EMOJI.search(fine), fine)
