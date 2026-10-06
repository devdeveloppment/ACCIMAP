import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from reports.zone import get_coverage_zone

LOME_CENTRE = (6.1319, 1.2228)
SQUARE = [[[1.20, 6.10], [1.25, 6.10], [1.25, 6.15], [1.20, 6.15], [1.20, 6.10]]]


def write_geojson(directory, payload, name="zone.geojson"):
    path = Path(directory) / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class IndicativeZoneTests(SimpleTestCase):
    def test_zone_par_defaut_non_officielle(self):
        zone = get_coverage_zone()
        self.assertFalse(zone.is_official)
        self.assertIn("non officielle", zone.label)
        self.assertIn("approximative", zone.disclaimer)
        self.assertIn("pas une limite administrative officielle", zone.disclaimer)

    def test_contient_lome_et_exclut_les_autres_villes(self):
        zone = get_coverage_zone()
        self.assertTrue(zone.contains(*LOME_CENTRE))
        self.assertTrue(zone.contains(*settings.MAP_DEFAULT_CENTER))
        for name, (lat, lon) in {"Aného": (6.23, 1.60), "Tsévié": (6.43, 1.21), "Accra": (5.60, -0.19)}.items():
            with self.subTest(ville=name):
                self.assertFalse(zone.contains(lat, lon))

    def test_geojson_pour_leaflet(self):
        feature = get_coverage_zone().as_feature()
        self.assertEqual(feature["type"], "Feature")
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        self.assertFalse(feature["properties"]["is_official"])


class OfficialZoneTests(SimpleTestCase):
    """Le polygone officiel se branche par fichier + variables d'environnement, sans toucher au code."""

    def test_fichier_geojson_remplace_la_zone_indicative(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_geojson(tmp, {"type": "Polygon", "coordinates": SQUARE})
            with override_settings(COVERAGE_ZONE_GEOJSON=path, COVERAGE_ZONE_IS_OFFICIAL=True,
                                   COVERAGE_ZONE_NAME="District Test"):
                zone = get_coverage_zone()
                self.assertTrue(zone.is_official)
                self.assertEqual(zone.label, "Zone de couverture : District Test")
                self.assertEqual(zone.disclaimer, "")
                self.assertTrue(zone.contains(6.12, 1.22))
                self.assertFalse(zone.contains(6.20, 1.30))  # dans le rectangle indicatif, hors du polygone

    def test_fichier_fourni_mais_non_declare_officiel_reste_non_officiel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_geojson(tmp, {"type": "Polygon", "coordinates": SQUARE})
            with override_settings(COVERAGE_ZONE_GEOJSON=path, COVERAGE_ZONE_IS_OFFICIAL=False):
                zone = get_coverage_zone()
                self.assertFalse(zone.is_official)
                self.assertIn("non officielle", zone.label)

    def test_feature_collection_et_multipolygone(self):
        other = [[[1.30, 6.20], [1.35, 6.20], [1.35, 6.25], [1.30, 6.25], [1.30, 6.20]]]
        payload = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": SQUARE}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": [other]}},
        ]}
        with tempfile.TemporaryDirectory() as tmp, override_settings(COVERAGE_ZONE_GEOJSON=write_geojson(tmp, payload)):
            zone = get_coverage_zone()
            self.assertTrue(zone.contains(6.12, 1.22))
            self.assertTrue(zone.contains(6.22, 1.32))
            self.assertFalse(zone.contains(6.18, 1.28))

    def test_chemin_relatif_resolu_depuis_la_racine_du_projet(self):
        path = settings.BASE_DIR / "data" / "_zone_test.geojson"
        path.write_text(json.dumps({"type": "Polygon", "coordinates": SQUARE}), encoding="utf-8")
        try:
            with override_settings(COVERAGE_ZONE_GEOJSON="data/_zone_test.geojson"):
                self.assertTrue(get_coverage_zone().contains(6.12, 1.22))
        finally:
            path.unlink()

    def test_fichiers_invalides_donnent_une_erreur_explicite(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_point = write_geojson(tmp, {"type": "Point", "coordinates": [1.2, 6.1]}, "point.geojson")
            empty = write_geojson(tmp, {"type": "FeatureCollection", "features": []}, "empty.geojson")
            broken = Path(tmp) / "broken.geojson"
            broken.write_text("{pas du json", encoding="utf-8")
            for path in [bad_point, empty, str(broken), str(Path(tmp) / "absent.geojson")]:
                with self.subTest(path=Path(path).name), override_settings(COVERAGE_ZONE_GEOJSON=path):
                    with self.assertRaises(ImproperlyConfigured):
                        get_coverage_zone()

    def test_le_cache_est_reinitialise_au_retour_aux_reglages_par_defaut(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(COVERAGE_ZONE_GEOJSON=write_geojson(tmp, {"type": "Polygon", "coordinates": SQUARE})):
                self.assertFalse(get_coverage_zone().contains(6.20, 1.30))
        self.assertTrue(get_coverage_zone().contains(6.20, 1.30))
