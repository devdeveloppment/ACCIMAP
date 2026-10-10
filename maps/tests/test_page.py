from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from reports.choices import ReportStatus
from reports.tests.helpers import make_report


class PublicMapPageTests(TestCase):
    URL = reverse("maps:public_map")

    def test_accessible_sans_connexion(self):
        self.assertEqual(self.client.get(self.URL).status_code, 200)

    def test_elements_de_la_carte(self):
        page = self.client.get(self.URL)
        for expected in ['id="public-map"', 'name="viewport"', "Marqueurs", "Heatmap", "Recentrer", "Réinitialiser",
                         'name="date_from"', 'name="date_to"', 'name="accident_type"', 'name="severity"',
                         "Collision", "Très grave", "vérifiés", 'id="map-config"']:
            self.assertContains(page, expected)

    def test_filtres_administrateur_absents_de_la_page_publique(self):
        page = self.client.get(self.URL).content.decode()
        for forbidden in ['name="status"', 'name="mode"', 'name="zone"', "anonyme", "Anonymes"]:
            self.assertNotIn(forbidden, page.replace("Signaler anonymement", ""), forbidden)

    def test_formulation_prudente_sur_la_heatmap(self):
        page = self.client.get(self.URL).content.decode()
        self.assertIn("zones à forte concentration de signalements", page)
        self.assertIn("non d'une preuve officielle", page.replace("&#x27;", "'"))
        self.assertNotIn("point noir", page.lower())

    def test_aucune_donnee_de_signalement_dans_le_html(self):
        make_report(status=ReportStatus.VERIFIED, description="DESCRIPTION-SECRETE", injured_count=77)
        page = self.client.get(self.URL).content.decode()
        self.assertNotIn("DESCRIPTION-SECRETE", page)
        self.assertNotIn("FeatureCollection", page)
        self.assertNotIn("coordinates", page)

    def test_filtres_initiaux_depuis_l_url(self):
        page = self.client.get(self.URL, {"accident_type": "RUN_OFF_ROAD", "severity": "SEVERE", "date_from": "2026-01-15"})
        self.assertContains(page, '<option value="RUN_OFF_ROAD" selected>')
        self.assertContains(page, '<option value="SEVERE" selected>')
        self.assertContains(page, 'value="2026-01-15"')

    def test_url_avec_filtres_invalides_ne_plante_pas(self):
        self.assertEqual(self.client.get(self.URL, {"severity": "NOPE", "date_from": "xx"}).status_code, 200)

    def test_configuration_javascript(self):
        page = self.client.get(self.URL)
        self.assertEqual(page.context["map_config"]["dataUrl"], "/map/data/")
        self.assertFalse(page.context["map_config"]["showDescription"])

    def test_fichiers_statiques_de_la_carte_presents(self):
        for path in ["vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css",
                     "vendor/leaflet-markercluster/leaflet.markercluster.js",
                     "vendor/leaflet-markercluster/MarkerCluster.css",
                     "vendor/leaflet-markercluster/MarkerCluster.Default.css",
                     "vendor/leaflet-heat/leaflet-heat.js", "js/public_map.js"]:
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path), path)

    def test_marqueurs_epingles_ancrees_sur_le_point_gps(self):
        script = finders.find("js/public_map.js")
        with open(script, encoding="utf-8") as source:
            javascript = source.read()
        self.assertIn('class="map-pin-svg"', javascript)
        self.assertIn('iconAnchor: [size / 2, height]', javascript)
        self.assertIn("popupAnchor: [0, -height]", javascript)
        stylesheet = finders.find("css/accimap.css")
        with open(stylesheet, encoding="utf-8") as source:
            css = source.read()
        for rule in (
            ".sev-critical { --sev: #d7191c; }",
            ".sev-severe   { --sev: #f06400; }",
            ".sev-medium   { --sev: #e6b000; }",
            ".sev-low      { --sev: #1a9641; }",
        ):
            self.assertIn(rule, css)
        self.assertIn(".map-pin-svg path { fill: var(--sev);", css)

    def test_epingles_utilisent_toujours_la_gravite_et_conservent_les_popups(self):
        script = finders.find("js/public_map.js")
        with open(script, encoding="utf-8") as source:
            javascript = source.read()
        self.assertIn('var cls = SEVERITY_CLASS[p.severity] || "sev-medium";', javascript)
        self.assertNotIn("STATUS_CLASS[p.status]", javascript)
        self.assertIn('className: "sev-marker " + cls', javascript)
        self.assertIn('marker.bindPopup(function () { return popupContent(p); }', javascript)
        self.assertIn("severity-neutral-cluster", javascript)


class HomePageTests(TestCase):
    def test_numeros_d_urgence_sur_accueil_et_a_propos_dans_l_ordre(self):
        for url_name in ("home", "about"):
            with self.subTest(page=url_name):
                page = self.client.get(reverse(url_name))
                self.assertEqual(page.status_code, 200)
                html = page.content.decode()
                labels = ("Police", "Pompiers", "SAMU", "Urgences")
                numbers = ("111", "185", "144", "112")
                positions = [html.index(label) for label in labels]
                self.assertEqual(positions, sorted(positions))
                for label, number in zip(labels, numbers):
                    self.assertIn(f'href="tel:{number}"', html)
                    self.assertIn(f">{number}</strong>", html)

    def test_acces_demandes(self):
        page = self.client.get(reverse("home"))
        for expected in ["Signaler un accident", "Voir la carte",
                         "Qu'est-ce qu'ACCIMAP", "Comment signaler ?", "Ce que montre la carte"]:
            self.assertContains(page, expected)
        self.assertNotContains(page, "Signaler anonymement")
        self.assertNotContains(page, "Se connecter avec mon téléphone")
        self.assertContains(page, reverse("reports:create"))
        anonymous_form = self.client.get(reverse("reports:anonymous_create"))
        self.assertEqual(anonymous_form.status_code, 200)
        self.assertContains(page, reverse("maps:public_map"))

    def test_le_bouton_signaler_est_l_action_principale(self):
        page = self.client.get(reverse("home"))
        html = page.content.decode()
        self.assertContains(page, 'id="cta-report"')
        self.assertContains(page, "btn-cta")
        self.assertContains(page, "4 étapes simples")
        self.assertNotContains(page, 'id="cta-anonymous"')
        self.assertNotContains(page, "Se connecter avec mon téléphone")

    def test_les_quatre_etapes_correspondent_au_parcours_reel(self):
        html = self.client.get(reverse("home")).content.decode()
        for expected in ("Choisissez le type", "Indiquez le lieu", "Ajoutez des détails", "Vérifiez et envoyez"):
            self.assertIn(expected, html)
        self.assertGreaterEqual(html.count('class="step-card-icon"'), 4)     # une icône par étape

    def test_pas_de_chiffre_quand_il_n_y_a_rien_a_montrer(self):
        self.assertNotContains(self.client.get(reverse("home")), 'id="home-stats"')

    def test_le_chiffre_ne_compte_que_les_verifies(self):
        make_report(status=ReportStatus.VERIFIED)
        make_report(status=ReportStatus.PENDING)
        make_report(status=ReportStatus.PENDING)
        make_report(status=ReportStatus.REJECTED)
        page = self.client.get(reverse("home"))
        self.assertContains(page, 'id="home-stats"')
        self.assertContains(page, '<span class="stat-number">1</span>')
        self.assertContains(page, "signalement vérifié affiché")

    def test_pluriel(self):
        make_report(status=ReportStatus.VERIFIED)
        make_report(status=ReportStatus.VERIFIED)
        self.assertContains(self.client.get(reverse("home")), "signalements vérifiés affichés")

    def test_aucune_donnee_privee_sur_l_accueil(self):
        make_report(status=ReportStatus.VERIFIED, description="DESCRIPTION-SECRETE")
        self.assertNotContains(self.client.get(reverse("home")), "DESCRIPTION-SECRETE")
