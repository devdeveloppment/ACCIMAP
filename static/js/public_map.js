/* Carte publique : signalements VÉRIFIÉS uniquement, chargés dynamiquement (GeoJSON).
   Aucune donnée privée n'existe côté navigateur : le serveur ne les envoie jamais. */
(function () {
  "use strict";

  var cfg = JSON.parse(document.getElementById("map-config").textContent);
  var form = document.getElementById("map-filters");
  var countEl = document.getElementById("map-count");
  var alertEl = document.getElementById("map-alert");
  var loadingEl = document.getElementById("map-loading");
  var toggleMarkers = document.getElementById("toggle-markers");
  var toggleHeat = document.getElementById("toggle-heat");
  var dateFrom = form.elements.date_from, dateTo = form.elements.date_to;

  var SEVERITY_CLASS = { LOW: "sev-low", MEDIUM: "sev-medium", SEVERE: "sev-severe", CRITICAL: "sev-critical" };
  // Mode administrateur : même carte, mais tous les statuts, marqueurs colorés par statut.
  var ADMIN = !!cfg.admin;
  var STATUS_CLASS = { PENDING: "st-pending", VERIFIED: "st-verified", REJECTED: "st-rejected" };
  var STATUS_BADGE = { PENDING: "text-bg-warning", VERIFIED: "text-bg-success", REJECTED: "text-bg-secondary" };

  if (typeof L === "undefined") {
    showAlert("danger", "La carte n'a pas pu être chargée. Vérifiez votre connexion puis actualisez la page.");
    countEl.textContent = "";
    return;
  }

  /* ---------- Carte, fond OpenStreetMap ---------- */
  var tileWarned = false;
  var map = L.map("public-map", { center: cfg.center, zoom: cfg.zoom });
  L.tileLayer(cfg.tiles.url, { maxZoom: 19, attribution: cfg.tiles.attribution })
    .on("tileerror", function () {
      if (!tileWarned) {
        tileWarned = true;
        showAlert("warning", "Le fond de carte n'a pas pu être chargé (vérifiez votre connexion). Les signalements restent affichés.");
      }
    })
    .addTo(map);

  var cluster = L.markerClusterGroup({
    showCoverageOnHover: false, maxClusterRadius: 50, spiderfyOnMaxZoom: true, chunkedLoading: true
  });
  var HEAT_GRADIENT = { 0.2: "#2c7bb6", 0.45: "#abd9e9", 0.65: "#ffffbf", 0.85: "#fdae61", 1.0: "#d7191c" };
  // maxZoom = niveau à partir duquel l'intensité est pleine : en dessous, leaflet.heat l'atténue
  // (×2 par niveau). 12 correspond à l'échelle d'une ville, vue par défaut sur Lomé.
  var heat = L.heatLayer([], { radius: 26, blur: 20, maxZoom: 12, minOpacity: 0.3, max: 1.0, gradient: HEAT_GRADIENT });

  // Légende de la heatmap, affichée seulement quand la couche est active.
  var heatLegend = L.control({ position: "bottomleft" });
  heatLegend.onAdd = function () {
    var box = L.DomUtil.create("div", "heat-legend");
    box.appendChild(el("div", "heat-legend-title", "Concentration de signalements"));
    var bar = el("div", "heat-gradient");
    box.appendChild(bar);
    var scale = el("div", "heat-scale");
    scale.appendChild(el("span", null, "faible"));
    scale.appendChild(el("span", null, "forte"));
    box.appendChild(scale);
    return box;
  };

  /* ---------- Utilitaires DOM (jamais d'innerHTML avec des données) ---------- */
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }
  function showAlert(kind, text, retry) {
    alertEl.className = "alert alert-" + kind;
    alertEl.textContent = text;
    if (retry) {
      var button = el("button", "btn btn-sm btn-outline-dark ms-2", "Réessayer");
      button.type = "button";
      button.addEventListener("click", function () { load({ fit: true }); });
      alertEl.appendChild(button);
    }
  }
  function hideAlert() { alertEl.className = "alert d-none"; alertEl.textContent = ""; }
  function setLoading(on) { loadingEl.hidden = !on; }
  function formatDate(iso) { var p = iso.split("-"); return p[2] + "/" + p[1] + "/" + p[0]; }

  /* ---------- Marqueurs et popups (informations publiques uniquement) ---------- */
  function iconFor(p) {
    var cls = ADMIN ? STATUS_CLASS[p.status] : SEVERITY_CLASS[p.severity];
    return L.divIcon({
      className: "sev-marker " + (cls || "sev-medium"),
      iconSize: [24, 24], iconAnchor: [12, 12], popupAnchor: [0, -12]
    });
  }

  function popupRow(label, value) {
    var row = el("div", "small");
    row.appendChild(el("span", "text-muted", label + " : "));
    row.appendChild(document.createTextNode(value));
    return row;
  }

  function adminPopup(p) {
    var box = el("div", "map-popup");
    box.appendChild(el("div", "fw-bold", p.reference));
    box.appendChild(el("div", "mb-1", p.type_label));
    var badges = el("div", "mb-2");
    badges.appendChild(el("span", "badge " + (STATUS_BADGE[p.status] || ""), p.status_label));
    badges.appendChild(el("span", "badge sev-badge ms-1 " + (SEVERITY_CLASS[p.severity] || ""), p.severity_label));
    box.appendChild(badges);
    box.appendChild(popupRow("Date", formatDate(p.date) + " à " + p.time));
    box.appendChild(popupRow("Blessés", String(p.injured)));
    box.appendChild(popupRow("Décès", String(p.deaths)));
    box.appendChild(popupRow("Mode", p.anonymous ? "Anonyme" : "Identifié"));
    box.appendChild(popupRow("Zone", p.in_zone ? "Dans la zone de couverture" : "Hors zone de couverture"));
    var link = el("a", "btn btn-sm btn-primary mt-2", "Ouvrir le détail");
    link.href = cfg.detailUrl.replace("{id}", encodeURIComponent(p.id));
    box.appendChild(link);
    return box;
  }

  function popupContent(p) {
    if (ADMIN) { return adminPopup(p); }
    var box = el("div", "map-popup");
    box.appendChild(el("div", "fw-bold mb-1", p.type_label));
    var badges = el("div", "mb-2");
    badges.appendChild(el("span", "badge sev-badge " + (SEVERITY_CLASS[p.severity] || ""), p.severity_label));
    badges.appendChild(el("span", "badge text-bg-success ms-1", "Vérifié"));
    box.appendChild(badges);
    box.appendChild(popupRow("Date", formatDate(p.date) + " à " + p.time));
    box.appendChild(popupRow("Blessés", String(p.injured)));
    box.appendChild(popupRow("Décès", String(p.deaths)));
    if (cfg.showDescription && p.description) {
      box.appendChild(el("p", "small mt-2 mb-0", p.description));
    }
    return box;
  }

  /* ---------- Chargement des données ---------- */
  var lastBounds = null, requestSeq = 0;
  // Derniers points de la heatmap. leaflet.heat plante si on appelle setLatLngs() sur une couche
  // retirée de la carte : on ne la met à jour que lorsqu'elle est affichée.
  var latestHeatPoints = [];

  function currentQuery() {
    var params = new URLSearchParams();
    new FormData(form).forEach(function (value, key) { if (value) { params.append(key, value); } });
    return params.toString();
  }

  function render(collection, fit) {
    var markers = [], heatPoints = [], bounds = L.latLngBounds([]);
    cluster.clearLayers();
    collection.features.forEach(function (feature) {
      var c = feature.geometry.coordinates;          // GeoJSON : [longitude, latitude]
      var latlng = [c[1], c[0]];
      var p = feature.properties;
      var title = ADMIN ? p.reference + " : " + p.status_label : p.type_label + " : " + p.severity_label;
      var marker = L.marker(latlng, { icon: iconFor(p), title: title });
      marker.bindPopup(function () { return popupContent(p); }, { maxWidth: 260 });
      markers.push(marker);
      heatPoints.push([c[1], c[0], 0.4]);   // même poids pour chaque signalement
      bounds.extend(latlng);
    });
    cluster.addLayers(markers);
    latestHeatPoints = heatPoints;
    if (map.hasLayer(heat)) { heat.setLatLngs(latestHeatPoints); }
    lastBounds = markers.length ? bounds : null;

    var n = collection.features.length;
    var noun = ADMIN ? "signalement" : "signalement vérifié";
    if (n === 0) {
      countEl.textContent = "Aucun " + noun + " ne correspond à ces filtres.";
    } else {
      countEl.textContent = n + " " + noun + (n > 1 ? "s" : "") + (n > 1 ? " affichés" : " affiché") +
        (collection.meta && collection.meta.truncated ? " (affichage limité)" : "");
    }
    if (fit) { fitToData(); }
  }

  function fitToData() {
    if (lastBounds) { map.fitBounds(lastBounds.pad(0.2), { maxZoom: 15 }); }
    else { map.setView(cfg.center, cfg.zoom); }
  }

  function load(options) {
    var seq = ++requestSeq;
    var query = currentQuery();
    hideAlertUnlessTiles();
    setLoading(true);
    history.replaceState(null, "", location.pathname + (query ? "?" + query : ""));

    fetch(cfg.dataUrl + (query ? "?" + query : ""), { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (response) {
        return response.json().then(function (body) { return { ok: response.ok, body: body }; });
      })
      .then(function (result) {
        if (seq !== requestSeq) { return; }
        if (!result.ok) {
          var fields = (result.body && result.body.fields) || {};
          var first = Object.keys(fields)[0];
          showAlert("warning", first ? fields[first][0] : "Filtres invalides.");
          return;
        }
        render(result.body, options && options.fit);
      })
      .catch(function (error) {
        console.error("Carte : échec du chargement des signalements", error);  // utile au diagnostic
        if (seq !== requestSeq) { return; }
        countEl.textContent = "";
        showAlert("danger", "Impossible de charger les signalements pour le moment.", true);
      })
      .then(function () { if (seq === requestSeq) { setLoading(false); } });
  }

  function hideAlertUnlessTiles() { if (!tileWarned) { hideAlert(); } else { alertEl.className = "alert alert-warning"; } }

  /* ---------- Couches ---------- */
  function applyLayers() {
    if (toggleMarkers.checked) { map.addLayer(cluster); } else { map.removeLayer(cluster); }
    if (toggleHeat.checked) {
      map.addLayer(heat);
      heat.setLatLngs(latestHeatPoints);   // après l'ajout : la couche est maintenant sur la carte
      heatLegend.addTo(map);
    }
    else { map.removeLayer(heat); heatLegend.remove(); }
  }
  toggleMarkers.addEventListener("change", applyLayers);
  toggleHeat.addEventListener("change", applyLayers);
  document.getElementById("fit-btn").addEventListener("click", fitToData);

  /* ---------- Filtres ---------- */
  var timer = null;
  function syncDateBounds() {
    dateTo.min = dateFrom.value || "";
    dateFrom.max = dateTo.value || "";
  }
  form.addEventListener("change", function () {
    syncDateBounds();
    clearTimeout(timer);
    timer = setTimeout(function () { load({ fit: false }); }, 250);
  });
  form.addEventListener("reset", function () {
    // l'événement précède la remise à zéro des champs : on attend qu'elle soit faite
    setTimeout(function () { syncDateBounds(); load({ fit: true }); }, 0);
  });

  /* ---------- Démarrage ---------- */
  applyLayers();
  syncDateBounds();
  load({ fit: true });
})();
