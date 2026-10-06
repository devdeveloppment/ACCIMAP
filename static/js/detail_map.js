/* Mini-carte de la page de détail d'un signalement (administrateur). */
(function () {
  "use strict";
  var container = document.getElementById("detail-map");
  var configNode = document.getElementById("map-config");
  if (!container || !configNode || typeof L === "undefined") { return; }
  var cfg = JSON.parse(configNode.textContent);

  var map = L.map(container, { center: cfg.position, zoom: 16 });
  L.tileLayer(cfg.tiles.url, { maxZoom: 19, attribution: cfg.tiles.attribution }).addTo(map);
  L.marker(cfg.position, {
    keyboard: false,
    icon: new L.Icon({
      iconUrl: cfg.icons.marker, iconRetinaUrl: cfg.icons.marker2x, shadowUrl: cfg.icons.shadow,
      iconSize: [25, 41], iconAnchor: [12, 41], shadowSize: [41, 41]
    })
  }).addTo(map);
})();
