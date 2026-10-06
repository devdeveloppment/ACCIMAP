/* Gestion des utilisateurs : les droits détaillés ne concernent que le rôle « Staff ». */
(function () {
  "use strict";
  var box = document.getElementById("staff-permissions");
  var radios = document.querySelectorAll('input[name="role"]');
  if (!box || !radios.length) { return; }

  function update() {
    var checked = document.querySelector('input[name="role"]:checked');
    box.hidden = !(checked && checked.value === "staff");
  }
  radios.forEach(function (radio) { radio.addEventListener("change", update); });
  update();
})();
