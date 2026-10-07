/* Parcours de signalement en 4 étapes : Type -> Lieu -> Détails -> Envoi.
   Sans JavaScript, toutes les étapes restent visibles (repli) ; le serveur revalide TOUT à l'envoi
   (type, coordonnées, zone, dates, nombres, photo) : ce script n'est qu'une aide à la saisie. */
(function () {
  "use strict";

  var MSG = {
    unsupported: "Votre navigateur ne permet pas la géolocalisation. Touchez la carte pour indiquer le lieu.",
    insecure: "La géolocalisation exige une connexion sécurisée (HTTPS). Touchez la carte pour indiquer le lieu.",
    denied: "Veuillez autoriser l'accès à votre position (dans les paramètres du navigateur). Vous pouvez aussi toucher la carte.",
    unavailable: "Votre position est indisponible (GPS désactivé ou signal insuffisant). Touchez la carte pour indiquer le lieu.",
    timeout: "La recherche de votre position a pris trop de temps. Réessayez ou touchez la carte.",
    unknown: "Votre position n'a pas pu être déterminée. Touchez la carte pour indiquer le lieu.",
    locating: "Recherche de votre position…",
    found: "Position trouvée. Vérifiez le repère et corrigez-le si besoin.",
    manual: "Position choisie. Vous pouvez faire glisser le repère pour la corriger.",
    missing: "Veuillez indiquer le lieu de l'accident : utilisez votre position ou touchez la carte.",
    unconfirmed: "Confirmez la position pour continuer.",
    noMap: "La carte n'a pas pu être chargée. Utilisez « Utiliser ma position actuelle » ou saisissez les coordonnées.",
    tiles: "Le fond de carte n'a pas pu être chargé (vérifiez votre connexion). Vous pouvez néanmoins placer le repère.",
    leadFound: "Nous avons trouvé votre position.",
    leadManual: "Touchez la carte pour indiquer où l'accident s'est produit.",
    placeLoading: "Recherche du quartier…",
    placeNone: "Position précise enregistrée (quartier non disponible).",
    confirm: "Confirmer cette position",
    confirmAnyway: "Confirmer malgré tout",
    photoSize: "Cette photo est trop volumineuse.",
    photoType: "Format non accepté : choisissez une photo JPEG, PNG ou WebP.",
    lowAccuracy: function (m) {
      return "Position trouvée, mais peu précise (environ " + m + " m). Vérifiez le repère et déplacez-le si besoin.";
    }
  };
  var TITLES = ["Type d'accident", "Lieu", "Détails", "Envoi"];

  var form = document.getElementById("report-form");
  var configNode = document.getElementById("map-config");
  if (!form || !configNode) { return; }
  var cfg = JSON.parse(configNode.textContent);

  var steps = Array.prototype.slice.call(form.querySelectorAll(".report-step"));
  var stepperItems = Array.prototype.slice.call(document.querySelectorAll("#stepper .stepper-item"));
  var progressBar = document.getElementById("wizard-progress-bar");
  var progress = document.getElementById("wizard-progress");
  var header = document.querySelector(".wizard-header");

  var latInput = document.getElementById("id_latitude");
  var lonInput = document.getElementById("id_longitude");
  var statusEl = document.getElementById("location-status");
  var leadEl = document.getElementById("location-lead");
  var placeEl = document.getElementById("place-label");
  var gpsBtn = document.getElementById("gps-btn");
  var confirmBtn = document.getElementById("confirm-position-btn");
  var confirmLabel = document.getElementById("confirm-position-label");
  var outsideBox = document.getElementById("outside-zone-box");
  var outsideDisclaimer = document.getElementById("outside-zone-disclaimer");
  var confirmInput = document.getElementById("id_confirm_outside_zone");
  var typeError = document.getElementById("type-error");
  var gpsLabel = gpsBtn.innerHTML;

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var state = { current: 1, furthest: 1, confirmed: false, autoLocated: false, placeText: "" };
  var hasMap = typeof L !== "undefined";
  var map = null, marker = null, accuracyCircle = null, markerIcon = null, tileWarned = false, pointerFlag = false;

  /* ------------------------------------------------------------------ utilitaires */
  function $(selector, root) { return (root || document).querySelector(selector); }
  function checked(name) { return form.querySelector('input[name="' + name + '"]:checked'); }
  function hasPosition() { return latInput.value !== "" && lonInput.value !== ""; }
  function outsideNow() { return !outsideBox.hidden; }
  function showStatus(kind, text) { statusEl.className = "alert alert-" + kind; statusEl.textContent = text; }
  function clearStatus() { statusEl.className = "alert d-none"; statusEl.textContent = ""; }
  function formatDate(iso) { var p = iso.split("-"); return p.length === 3 ? p[2] + "/" + p[1] + "/" + p[0] : iso; }

  // Avec JavaScript, la position se choisit via la carte / le GPS : champs en lecture seule.
  latInput.readOnly = true;
  lonInput.readOnly = true;

  /* ------------------------------------------------------------------ navigation entre étapes */
  function showStep(n, opts) {
    opts = opts || {};
    state.current = n;
    steps.forEach(function (step) { step.classList.toggle("is-active", Number(step.dataset.step) === n); });
    stepperItems.forEach(function (item) {
      var index = Number(item.dataset.step);
      item.classList.toggle("is-active", index === n);
      item.classList.toggle("is-done", index < n);
      item.querySelector(".stepper-btn").disabled = index > state.furthest;
      if (index === n) { item.setAttribute("aria-current", "step"); } else { item.removeAttribute("aria-current"); }
    });
    progressBar.style.width = (n / steps.length * 100) + "%";
    progress.setAttribute("aria-valuenow", String(n));
    progress.setAttribute("aria-valuetext", "Étape " + n + " sur " + steps.length + " : " + TITLES[n - 1]);

    if (n === 2) { enterLocationStep(); }
    if (n === 4) { buildRecap(); }
    if (opts.push) { history.pushState({ step: n }, "", "#etape-" + n); }

    var heading = $("[data-step='" + n + "'] .step-title", form);
    if (heading) { heading.focus({ preventScroll: true }); }
    if (!opts.silent && header) { header.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" }); }
  }

  function validate(n, quiet) {
    if (n === 1) {
      var ok = !!checked("accident_type");
      typeError.hidden = ok || quiet;
      return ok;
    }
    if (n === 2) {
      if (!hasPosition()) { if (!quiet) { showStatus("danger", MSG.missing); } return false; }
      if (!state.confirmed) { if (!quiet) { showStatus("warning", MSG.unconfirmed); } return false; }
      return true;
    }
    if (n === 3) {
      var fields = Array.prototype.slice.call(steps[2].querySelectorAll("input, select, textarea"));
      for (var i = 0; i < fields.length; i += 1) {
        if (!fields[i].checkValidity()) {
          if (!quiet) { fields[i].reportValidity(); }
          return false;
        }
      }
      return true;
    }
    return true;
  }

  function go(target) {
    if (target > state.current) {
      for (var i = state.current; i < target; i += 1) {
        if (!validate(i)) { if (i !== state.current) { showStep(i, { push: true }); } return; }
      }
      state.furthest = Math.max(state.furthest, target);
    }
    if (target !== state.current) { showStep(target, { push: true }); }
  }

  Array.prototype.forEach.call(form.querySelectorAll("[data-next]"), function (button) {
    button.addEventListener("click", function () { go(state.current + 1); });
  });
  Array.prototype.forEach.call(form.querySelectorAll("[data-prev]"), function (button) {
    button.addEventListener("click", function () { go(state.current - 1); });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-goto]"), function (button) {
    button.addEventListener("click", function () { go(Number(button.dataset.goto)); });
  });
  window.addEventListener("popstate", function (event) {
    var n = event.state && event.state.step;
    if (n && n <= state.furthest) { showStep(n, { silent: true }); }
  });

  // « Entrée » ne doit pas envoyer le formulaire avant la dernière étape.
  form.addEventListener("keydown", function (event) {
    var tag = event.target.tagName;
    if (event.key === "Enter" && tag !== "TEXTAREA" && tag !== "BUTTON" && event.target.type !== "submit") {
      event.preventDefault();
      if (state.current < steps.length) { go(state.current + 1); }
    }
  });
  form.addEventListener("submit", function (event) {
    if (state.current !== steps.length) { event.preventDefault(); go(state.current + 1); return; }
    for (var i = 1; i <= 3; i += 1) {
      if (!validate(i, true)) { event.preventDefault(); showStep(i, { push: true }); validate(i); return; }
    }
  });

  /* ------------------------------------------------------------------ étape 1 : type */
  var typeGrid = document.getElementById("type-grid");
  var step1Next = document.getElementById("step1-next");
  function syncType() { step1Next.disabled = !checked("accident_type"); typeError.hidden = true; }
  // Le saut automatique ne concerne que le toucher/clic : au clavier, on passe par « Continuer ».
  typeGrid.addEventListener("pointerdown", function () {
    pointerFlag = true;
    setTimeout(function () { pointerFlag = false; }, 700);
  });
  typeGrid.addEventListener("change", function () {
    syncType();
    if (pointerFlag && state.current === 1) { setTimeout(function () { if (state.current === 1) { go(2); } }, 260); }
  });
  syncType();

  /* ------------------------------------------------------------------ étape 2 : lieu */
  function setConfirmLabel() {
    confirmLabel.textContent = outsideNow() ? MSG.confirmAnyway : MSG.confirm;
  }

  function showOutside(isOutside, disclaimer) {
    outsideBox.hidden = !isOutside;
    confirmInput.required = false;      // la confirmation passe par le bouton ; le serveur reste juge
    if (!isOutside) { confirmInput.checked = false; }
    if (typeof disclaimer === "string") { outsideDisclaimer.textContent = disclaimer; }
    setConfirmLabel();
  }

  var zoneTimer = null, zoneSeq = 0;
  function checkZone(lat, lng) {
    clearTimeout(zoneTimer);
    zoneTimer = setTimeout(function () {
      var seq = ++zoneSeq;
      fetch(cfg.checkUrl + "?latitude=" + lat + "&longitude=" + lng, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (data) { if (seq === zoneSeq) { showOutside(!data.inside_zone, data.disclaimer); } })
        .catch(function () { /* hors ligne : le serveur vérifiera à l'envoi */ });
    }, 200);
  }

  var placeTimer = null, placeSeq = 0;
  function lookupPlace(lat, lng) {
    clearTimeout(placeTimer);
    state.placeText = "";
    placeEl.textContent = MSG.placeLoading;
    placeTimer = setTimeout(function () {
      var seq = ++placeSeq;
      fetch(cfg.placeUrl + "?latitude=" + lat + "&longitude=" + lng, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (data) {
          if (seq !== placeSeq) { return; }
          state.placeText = data.place || "";
          placeEl.textContent = "";
          if (data.place) {
            var strong = document.createElement("strong");
            strong.textContent = data.place;
            placeEl.appendChild(strong);
          } else {
            var muted = document.createElement("span");
            muted.className = "text-muted";
            muted.textContent = MSG.placeNone;
            placeEl.appendChild(muted);
          }
        })
        .catch(function () {
          if (seq === placeSeq) { placeEl.textContent = ""; var s = document.createElement("span"); s.className = "text-muted"; s.textContent = MSG.placeNone; placeEl.appendChild(s); }
        });
    }, 700);
  }

  function removeAccuracy() { if (accuracyCircle && map) { map.removeLayer(accuracyCircle); accuracyCircle = null; } }

  function setPosition(lat, lng) {
    lat = Math.round(lat * 1e6) / 1e6;   // 6 décimales ≈ 11 cm
    lng = Math.round(lng * 1e6) / 1e6;
    latInput.value = lat.toFixed(6);
    lonInput.value = lng.toFixed(6);
    $("#coords-summary").classList.add("has-position");
    confirmInput.checked = false;        // la confirmation porte sur UNE position précise
    state.confirmed = false;
    confirmBtn.disabled = false;
    if (hasMap) {
      if (!marker) {
        marker = L.marker([lat, lng], { draggable: true, icon: markerIcon, keyboard: false }).addTo(map);
        marker.on("dragend", function () {
          var p = marker.getLatLng().wrap();
          removeAccuracy();
          setPosition(p.lat, p.lng);
          showStatus("info", MSG.manual);
        });
      } else {
        marker.setLatLng([lat, lng]);
      }
    }
    checkZone(lat, lng);
    lookupPlace(lat, lng);
  }

  function ensureMap() {
    if (map || !hasMap) { return; }
    map = L.map("location-map", { center: cfg.center, zoom: cfg.zoom });
    // OSM exige un en-tête Referer (sinon tuiles « 403 Access blocked ») : on envoie seulement l'origine du site.
    L.tileLayer(cfg.tiles.url, { maxZoom: 19, attribution: cfg.tiles.attribution, referrerPolicy: "strict-origin-when-cross-origin" })
      .on("tileerror", function () { if (!tileWarned) { tileWarned = true; showStatus("warning", MSG.tiles); } })
      .addTo(map);
    // Zone de couverture : tracé pointillé, légendé comme indicatif tant qu'elle n'est pas officielle.
    L.geoJSON(cfg.zone, { style: { color: "#c2570a", weight: 2, dashArray: "6 6", fill: false, interactive: false } }).addTo(map);
    markerIcon = new L.Icon({
      iconUrl: cfg.icons.marker, iconRetinaUrl: cfg.icons.marker2x, shadowUrl: cfg.icons.shadow,
      iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41]
    });
    map.on("click", function (e) {
      var p = e.latlng.wrap();
      removeAccuracy();
      setPosition(p.lat, p.lng);
      showStatus("info", MSG.manual);
    });
    if (hasPosition()) {
      setPosition(parseFloat(latInput.value), parseFloat(lonInput.value));
      map.setView([parseFloat(latInput.value), parseFloat(lonInput.value)], 16);
    }
  }

  function endLocating() { gpsBtn.disabled = false; gpsBtn.innerHTML = gpsLabel; }

  function locate() {
    if (!("geolocation" in navigator)) { showStatus("warning", MSG.unsupported); leadEl.textContent = MSG.leadManual; return; }
    if (window.isSecureContext === false) { showStatus("warning", MSG.insecure); leadEl.textContent = MSG.leadManual; return; }
    gpsBtn.disabled = true;
    gpsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' + MSG.locating;
    showStatus("info", MSG.locating);

    navigator.geolocation.getCurrentPosition(
      function (pos) {
        endLocating();
        var lat = pos.coords.latitude, lng = pos.coords.longitude;
        setPosition(lat, lng);
        leadEl.textContent = MSG.leadFound;
        if (hasMap) {
          removeAccuracy();
          if (pos.coords.accuracy) {
            accuracyCircle = L.circle([lat, lng], { radius: pos.coords.accuracy, color: "#1d5f9a", weight: 1, fillOpacity: 0.1, interactive: false }).addTo(map);
          }
          map.setView([lat, lng], Math.max(map.getZoom(), 17));
        }
        var accuracy = Math.round(pos.coords.accuracy || 0);
        if (accuracy > 100) { showStatus("warning", MSG.lowAccuracy(accuracy)); } else { showStatus("success", MSG.found); }
      },
      function (err) {
        endLocating();
        // Dans tous les cas, la sélection manuelle sur la carte reste possible.
        var text = MSG.unknown;
        if (err && err.code === 1) { text = MSG.denied; }
        else if (err && err.code === 2) { text = MSG.unavailable; }
        else if (err && err.code === 3) { text = MSG.timeout; }
        showStatus("warning", text);
        leadEl.textContent = MSG.leadManual;
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
    );
  }

  function enterLocationStep() {
    ensureMap();
    if (map) { setTimeout(function () { map.invalidateSize(); }, 60); }
    if (state.autoLocated) { return; }
    state.autoLocated = true;
    if (hasPosition()) { return; }       // formulaire réaffiché avec une position déjà choisie
    // Tentative automatique, sauf si le navigateur a déjà mémorisé un refus (inutile de réessayer).
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({ name: "geolocation" }).then(function (result) {
        if (result.state === "denied") { showStatus("warning", MSG.denied); leadEl.textContent = MSG.leadManual; }
        else { locate(); }
      }).catch(locate);
    } else {
      locate();
    }
  }

  gpsBtn.addEventListener("click", locate);

  confirmBtn.addEventListener("click", function () {
    if (!hasPosition()) { showStatus("danger", MSG.missing); return; }
    if (outsideNow()) { confirmInput.checked = true; }
    state.confirmed = true;
    clearStatus();
    go(3);
  });

  // Case de confirmation (repli sans bouton) : même effet que le bouton.
  confirmInput.addEventListener("change", function () { state.confirmed = confirmInput.checked || !outsideNow(); });

  if (!hasMap) {
    latInput.readOnly = false;
    lonInput.readOnly = false;
    confirmBtn.disabled = false;
    showStatus("warning", MSG.noMap);
  }

  /* ------------------------------------------------------------------ étape 3 : détails */
  Array.prototype.forEach.call(form.querySelectorAll(".counter"), function (counter) {
    var input = counter.querySelector("input");
    Array.prototype.forEach.call(counter.querySelectorAll("[data-delta]"), function (button) {
      button.addEventListener("click", function () {
        if (input.value === "" && Number(button.dataset.delta) < 0) { return; }   // facultatif : « diminuer » ne crée pas de valeur
        var min = input.min !== "" ? Number(input.min) : 0;
        var max = input.max !== "" ? Number(input.max) : 9999;
        var value = (parseInt(input.value, 10) || 0) + Number(button.dataset.delta);
        input.value = Math.min(max, Math.max(min, value));
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
  });

  /* ------------------------------------------------------------------ photos multiples */
  var photoInput = document.getElementById("id_photos");
  var photoGrid = document.getElementById("photo-grid");
  var photoError = document.getElementById("photo-error");
  var photoCount = document.getElementById("photo-count");
  var dropzone = document.getElementById("photo-dropzone");
  var maxPhotos = cfg.maxPhotos || 5;
  // La liste de fichiers d'un <input> n'est pas modifiable directement : on garde notre
  // propre tableau et on le recopie dans l'input via un DataTransfer (permet la suppression).
  var photoFiles = [];
  var photoUrls = [];

  function syncInputFiles() {
    var dt = new DataTransfer();
    photoFiles.forEach(function (file) { dt.items.add(file); });
    photoInput.files = dt.files;
  }

  function clearPhotoUrls() {
    photoUrls.forEach(function (url) { URL.revokeObjectURL(url); });
    photoUrls = [];
  }

  function renderPhotos() {
    clearPhotoUrls();
    photoGrid.textContent = "";
    photoFiles.forEach(function (file, index) {
      var url = URL.createObjectURL(file);
      photoUrls.push(url);
      var cell = document.createElement("div");
      cell.className = "photo-thumb";
      var img = document.createElement("img");
      img.src = url;
      img.alt = "Aperçu : " + file.name;
      cell.appendChild(img);
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "photo-thumb-remove";
      remove.setAttribute("aria-label", "Retirer cette photo");
      remove.textContent = "×";
      remove.addEventListener("click", function () {
        photoFiles.splice(index, 1);
        syncInputFiles();
        renderPhotos();
      });
      cell.appendChild(remove);
      photoGrid.appendChild(cell);
    });
    var n = photoFiles.length;
    photoGrid.hidden = n === 0;
    photoCount.hidden = n === 0;
    photoCount.textContent = n + " photo" + (n > 1 ? "s" : "") + " sur " + maxPhotos;
    dropzone.classList.toggle("is-full", n >= maxPhotos);
  }

  function addPhotos(fileList) {
    photoError.hidden = true;
    var rejected = null;
    Array.prototype.forEach.call(fileList, function (file) {
      if (photoFiles.length >= maxPhotos) { rejected = "Vous pouvez joindre au maximum " + maxPhotos + " photos."; return; }
      if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { rejected = MSG.photoType; return; }
      if (file.size > cfg.maxUploadBytes) { rejected = MSG.photoSize + " (maximum " + Math.round(cfg.maxUploadBytes / 1048576) + " Mo)."; return; }
      photoFiles.push(file);
    });
    syncInputFiles();
    renderPhotos();
    if (rejected) { photoError.textContent = rejected; photoError.hidden = false; }
  }

  photoInput.addEventListener("change", function () {
    // Les fichiers choisis s'ajoutent à la sélection existante (sans écraser).
    if (photoInput.files && photoInput.files.length) {
      var picked = Array.prototype.slice.call(photoInput.files);
      addPhotos(picked);   // addPhotos recopie l'ensemble (anciens + nouveaux) dans l'input
    }
  });

  // Glisser-déposer (desktop) : surbrillance + ajout des fichiers déposés.
  ["dragenter", "dragover"].forEach(function (evt) {
    dropzone.addEventListener(evt, function (e) { e.preventDefault(); dropzone.classList.add("is-dragover"); });
  });
  ["dragleave", "drop"].forEach(function (evt) {
    dropzone.addEventListener(evt, function (e) { e.preventDefault(); dropzone.classList.remove("is-dragover"); });
  });
  dropzone.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) { addPhotos(e.dataTransfer.files); }
  });

  /* ------------------------------------------------------------------ étape 4 : récapitulatif */
  function buildRecap() {
    var type = checked("accident_type");
    $("#recap-type").textContent = type ? type.dataset.label : "—";
    var icon = type ? type.closest(".type-card").querySelector(".type-icon") : null;
    $("#recap-type-icon").innerHTML = icon ? icon.innerHTML : "";

    var place = $("#recap-place");
    place.textContent = state.placeText || "Position choisie sur la carte";
    $("#recap-coords").textContent = hasPosition() ? latInput.value + ", " + lonInput.value : "—";

    var date = $("#id_accident_date").value, time = $("#id_accident_time").value;
    $("#recap-datetime").textContent = (date ? formatDate(date) : "—") + (time ? " à " + time : "");

    var severity = form.querySelector('input[name="severity"]:checked');
    var severityLabel = severity ? form.querySelector('label[for="' + severity.id + '"]') : null;
    var recapSeverity = $("#recap-severity");
    recapSeverity.textContent = "";
    if (severityLabel) {
      var SEV_CLASS = { LOW: "sev-low", MEDIUM: "sev-medium", SEVERE: "sev-severe", CRITICAL: "sev-critical" };
      var dot = document.createElement("span");
      dot.className = "sev-dot " + (SEV_CLASS[severity.value] || "sev-medium");
      recapSeverity.appendChild(dot);
      recapSeverity.appendChild(document.createTextNode(severityLabel.textContent.trim()));
    } else {
      recapSeverity.textContent = "—";
    }

    var vehicles = $("#id_vehicle_count").value;
    $("#recap-counts").textContent = "Véhicules : " + (vehicles === "" ? "non renseigné" : vehicles) +
      "  ·  Blessés : " + ($("#id_injured_count").value || 0) + "  ·  Décès : " + ($("#id_death_count").value || 0);

    var description = $("#id_description").value.trim();
    $("#recap-description-row").hidden = !description;
    $("#recap-description").textContent = description;

    var recapPhotos = $("#recap-photos");
    recapPhotos.textContent = "";
    $("#recap-photo-row").hidden = photoFiles.length === 0;
    if (photoFiles.length) {
      $("#recap-photo-count").textContent = photoFiles.length + " photo" + (photoFiles.length > 1 ? "s" : "") + " jointe" + (photoFiles.length > 1 ? "s" : "");
      photoFiles.forEach(function (file) {
        var img = document.createElement("img");
        img.className = "recap-photo";
        img.alt = "Photo jointe";
        img.src = URL.createObjectURL(file);
        recapPhotos.appendChild(img);
      });
    }
    $("#recap-declarant").textContent = form.dataset.declarant;
  }

  /* ------------------------------------------------------------------ démarrage */
  function firstStepWithErrors() {
    for (var i = 0; i < steps.length; i += 1) {
      if (steps[i].querySelector(".invalid-feedback:not([hidden])")) { return i + 1; }
    }
    return form.querySelector(".alert-danger") ? 3 : 0;
  }

  var startStep = 1;
  var errorStep = firstStepWithErrors();
  if (errorStep) {
    // Formulaire réaffiché par le serveur : on garde la saisie et on ouvre l'étape à corriger.
    startStep = errorStep;
    state.furthest = steps.length;
    state.confirmed = hasPosition() && (!outsideNow() || confirmInput.checked) && errorStep !== 2;
    if (hasPosition()) { confirmBtn.disabled = false; $("#coords-summary").classList.add("has-position"); }
    if (outsideNow()) { setConfirmLabel(); }
    if (errorStep === 2) { state.autoLocated = true; }
  }
  history.replaceState({ step: startStep }, "", "#etape-" + startStep);
  showStep(startStep, { silent: true });
})();
