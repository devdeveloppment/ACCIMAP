import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from reports import geocoding
from reports.geocoding import format_place, reverse_geocode

OSM = {"address": {"suburb": "Hédzranawoé", "city": "Lomé", "country": "Togo", "country_code": "tg"}}


def fake_response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    return response


class GeocodingTestMixin:
    def setUp(self):
        super().setUp()
        cache.clear()   # aucun résultat ni limitation (THROTTLE_KEY) hérités d'un autre test


class FormatPlaceTests(SimpleTestCase):
    def test_quartier_et_ville(self):
        self.assertEqual(format_place(OSM), "Hédzranawoé, Lomé")

    def test_ordre_de_preference_du_plus_fin_au_plus_large(self):
        payload = {"address": {"neighbourhood": "Nyékonakpoè", "suburb": "Autre", "town": "Lomé", "county": "Golfe"}}
        self.assertEqual(format_place(payload), "Nyékonakpoè, Lomé")

    def test_ville_seule_ou_quartier_seul(self):
        self.assertEqual(format_place({"address": {"city": "Lomé"}}), "Lomé")
        self.assertEqual(format_place({"address": {"village": "Baguida"}}), "Baguida")

    def test_doublon_quartier_ville(self):
        self.assertEqual(format_place({"address": {"suburb": "Lomé", "city": "Lomé"}}), "Lomé")

    def test_reponses_inutilisables(self):
        for payload in (None, {}, {"address": {}}, {"address": {"country": "Togo"}}, {"error": "Unable to geocode"}):
            self.assertIsNone(format_place(payload))


# Tests sans base de données (SimpleTestCase) : cache en mémoire, même comportement que le cache partagé en base.
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(REVERSE_GEOCODING_ENABLED=True, GEOCODER_URL="https://geocoder.test/reverse", GEOCODER_CONTACT="contact@accimap.test",
                   CACHES=LOCMEM_CACHE)
class ReverseGeocodeTests(GeocodingTestMixin, SimpleTestCase):
    @patch("reports.geocoding.urlopen")
    def test_appel_et_libelle(self, urlopen):
        urlopen.return_value = fake_response(OSM)
        self.assertEqual(reverse_geocode(6.1319, 1.2228), "Hédzranawoé, Lomé")
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith("https://geocoder.test/reverse?"))
        self.assertIn("lat=6.131900", request.full_url)
        self.assertIn("accept-language=fr", request.full_url)
        self.assertIn("ACCIMAP-prototype", request.get_header("User-agent"))
        self.assertIn("contact@accimap.test", request.get_header("User-agent"))
        self.assertEqual(urlopen.call_args.kwargs["timeout"], geocoding.TIMEOUT_SECONDS)

    @patch("reports.geocoding.urlopen")
    def test_aucune_donnee_d_identite_dans_la_requete(self, urlopen):
        urlopen.return_value = fake_response(OSM)
        reverse_geocode(6.1319, 1.2228)
        request = urlopen.call_args.args[0]
        self.assertEqual(sorted(k.lower() for k in request.headers), ["accept", "user-agent"])   # ni cookie ni IP

    @patch("reports.geocoding.urlopen")
    def test_cache_une_seconde_requete_ne_rappelle_pas_le_service(self, urlopen):
        urlopen.return_value = fake_response(OSM)
        reverse_geocode(6.1319, 1.2228)
        cache.delete(geocoding.THROTTLE_KEY)   # une seconde plus tard
        self.assertEqual(reverse_geocode(6.13191, 1.22281), "Hédzranawoé, Lomé")   # même cellule de ~11 m
        self.assertEqual(urlopen.call_count, 1)

    @patch("reports.geocoding.urlopen")
    def test_limitation_a_un_appel_par_seconde(self, urlopen):
        urlopen.return_value = fake_response(OSM)
        reverse_geocode(6.10, 1.20)
        self.assertIsNone(reverse_geocode(6.20, 1.30))       # trop rapproché : pas d'appel
        self.assertEqual(urlopen.call_count, 1)
        cache.delete(geocoding.THROTTLE_KEY)   # une seconde plus tard
        urlopen.return_value = fake_response(OSM)
        self.assertEqual(reverse_geocode(6.20, 1.30), "Hédzranawoé, Lomé")   # l'échec n'a pas été mis en cache

    @patch("reports.geocoding.urlopen", side_effect=URLError("réseau coupé"))
    def test_echec_reseau_jamais_d_exception(self, urlopen):
        self.assertIsNone(reverse_geocode(6.13, 1.22))

    @patch("reports.geocoding.urlopen", side_effect=TimeoutError())
    def test_delai_depasse(self, urlopen):
        self.assertIsNone(reverse_geocode(6.13, 1.22))

    @patch("reports.geocoding.urlopen")
    def test_reponse_invalide(self, urlopen):
        response = MagicMock()
        response.read.return_value = b"<html>pas du json</html>"
        response.__enter__.return_value = response
        urlopen.return_value = response
        self.assertIsNone(reverse_geocode(6.13, 1.22))

    @patch("reports.geocoding.urlopen")
    def test_echec_mis_en_cache_brievement(self, urlopen):
        urlopen.side_effect = URLError("x")
        reverse_geocode(6.13, 1.22)
        cache.delete(geocoding.THROTTLE_KEY)   # une seconde plus tard
        reverse_geocode(6.13, 1.22)
        self.assertEqual(urlopen.call_count, 1)                # pas de martèlement du service en panne

    @override_settings(REVERSE_GEOCODING_ENABLED=False)
    @patch("reports.geocoding.urlopen")
    def test_desactive(self, urlopen):
        self.assertIsNone(reverse_geocode(6.13, 1.22))
        urlopen.assert_not_called()


class PlaceEndpointTests(GeocodingTestMixin, TestCase):
    URL = reverse("reports:place")

    @patch("reports.views.reverse_geocode", return_value="Hédzranawoé, Lomé")
    def test_accessible_sans_connexion_et_retourne_le_lieu(self, lookup):
        response = self.client.get(self.URL, {"latitude": "6.13", "longitude": "1.22"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"place": "Hédzranawoé, Lomé"})
        lookup.assert_called_once_with(6.13, 1.22)

    @patch("reports.views.reverse_geocode", return_value=None)
    def test_service_indisponible_n_est_pas_une_erreur(self, lookup):
        response = self.client.get(self.URL, {"latitude": "6.13", "longitude": "1.22"})
        self.assertEqual((response.status_code, response.json()), (200, {"place": None}))

    @patch("reports.views.reverse_geocode")
    def test_parametres_invalides(self, lookup):
        for params in [{}, {"latitude": "6"}, {"latitude": "abc", "longitude": "1"},
                       {"latitude": "95", "longitude": "1"}, {"latitude": "6", "longitude": "181"}]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.URL, params).status_code, 400)
        lookup.assert_not_called()

    def test_get_uniquement(self):
        self.assertEqual(self.client.post(self.URL, {"latitude": "6", "longitude": "1"}).status_code, 405)

    @patch("reports.geocoding.urlopen", side_effect=AssertionError("aucun appel réseau attendu"))
    @override_settings(REVERSE_GEOCODING_ENABLED=False)
    def test_aucun_appel_reseau_quand_desactive(self, urlopen):
        self.assertEqual(self.client.get(self.URL, {"latitude": "6.13", "longitude": "1.22"}).json(), {"place": None})
