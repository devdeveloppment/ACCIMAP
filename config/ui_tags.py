"""
Icônes de l'interface : UNE seule famille (Lucide, licence ISC), en SVG intégré.

Utilisation dans n'importe quel gabarit (balise disponible partout, sans {% load %}) :
    {% icon "map-pin" %}                  taille par défaut 20 px
    {% icon "siren" size=28 css="me-2" %}

Le SVG hérite de la couleur du texte (currentColor). Pour ajouter une icône : copier son fichier .svg
depuis https://lucide.dev dans static/vendor/lucide/ ; un test vérifie que toutes les icônes citées existent.
"""
import re
from functools import lru_cache
from pathlib import Path

from django import template
from django.conf import settings
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

register = template.Library()
ICON_DIR = Path(settings.BASE_DIR) / "static" / "vendor" / "lucide"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BODY_RE = re.compile(r"<svg[^>]*>(.*)</svg>", re.DOTALL)


@lru_cache(maxsize=None)
def _icon_body(name):
    if not NAME_RE.match(name):
        raise template.TemplateSyntaxError(f"Nom d'icône invalide : {name!r}")
    path = ICON_DIR / f"{name}.svg"
    if not path.is_file():
        raise template.TemplateSyntaxError(
            f"Icône inconnue : {name!r} (ajoutez {path.name} dans static/vendor/lucide/)"
        )
    return BODY_RE.search(path.read_text(encoding="utf-8")).group(1).strip()


@register.simple_tag
def icon(name, size=20, css=""):
    classes = f"icon {conditional_escape(css)}".strip()
    return mark_safe(
        f'<svg class="{classes}" width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{_icon_body(name)}</svg>'
    )
