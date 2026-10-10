/* Graphiques du tableau de bord (Chart.js). Les données viennent du serveur, calculées par SQL. */
(function () {
  "use strict";

  var dataNode = document.getElementById("chart-data");
  if (!dataNode || typeof Chart === "undefined") { return; }
  var data = JSON.parse(dataNode.textContent);

  var SEVERITY = { LOW: "#2e9e5b", MEDIUM: "#e0a800", SEVERE: "#e8590c", CRITICAL: "#b02a37" };
  var STATUS = { PENDING: "#f0ad4e", VERIFIED: "#2e9e5b", REJECTED: "#6c757d" };
  var NAVY = "#0f2f4f", ACCENT = "#c2570a";

  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = "#495057";

  function total(values) { return values.reduce(function (a, b) { return a + b; }, 0); }

  function showEmpty(canvas) {
    var box = canvas.parentNode;
    box.textContent = "";
    var message = document.createElement("p");
    message.className = "chart-empty text-muted";
    message.textContent = "Aucune donnée pour ces critères.";
    box.appendChild(message);
  }

  function draw(id, series, config) {
    var canvas = document.getElementById(id);
    if (!canvas || !series) { return; }
    if (total(series.values) === 0) { showEmpty(canvas); return; }
    new Chart(canvas, config);
  }

  var integerAxis = { beginAtZero: true, ticks: { precision: 0 } };

  var period = data.period;
  draw("chart-period", period, {
    type: "bar",
    data: { labels: period.labels, datasets: [{ label: "Accidents", data: period.values, backgroundColor: NAVY, borderRadius: 3 }] },
    options: {
      responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: integerAxis, x: { ticks: { maxRotation: 0, autoSkip: true } } }
    }
  });

  draw("chart-types", data.types, {
    type: "bar",
    data: { labels: data.types.labels, datasets: [{ label: "Accidents", data: data.types.values,
      backgroundColor: data.types.colors, hoverBackgroundColor: data.types.colors, borderRadius: 3 }] },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { x: integerAxis, y: { ticks: { color: data.types.colors, font: { weight: 600 } } } }
    }
  });

  if (data.severities) {
    draw("chart-severities", data.severities, {
      type: "bar",
      data: {
        labels: data.severities.labels,
        datasets: [{ label: "Accidents", data: data.severities.values, borderRadius: 3,
          backgroundColor: data.severities.codes.map(function (c) { return SEVERITY[c]; }) }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: integerAxis } }
    });
  }

  function doughnut(id, series, colors) {
    draw(id, series, {
      type: "doughnut",
      data: {
        labels: series.labels.map(function (label, i) { return label + " (" + series.values[i] + ")"; }),
        datasets: [{ data: series.values, backgroundColor: colors, borderWidth: 2, borderColor: "#fff" }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } }
    });
  }

  doughnut("chart-statuses", data.statuses, data.statuses.codes.map(function (c) { return STATUS[c]; }));
  if (data.modes) { doughnut("chart-modes", data.modes, [NAVY, "#8aa4bf"]); }
})();
