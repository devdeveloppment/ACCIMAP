/* ACCIMAP : comportements communs. */
(function () {
  "use strict";

  // Formulaires marqués data-loading : empêche le double envoi et affiche un loader.
  var forms = document.querySelectorAll("form[data-loading]");

  forms.forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var button = form.querySelector('button[type="submit"]');
      if (!button) { return; }
      button.dataset.originalHtml = button.innerHTML;
      var text = button.dataset.loadingText || "Veuillez patienter…";
      // setTimeout : la désactivation ne doit pas empêcher l'envoi du formulaire.
      setTimeout(function () {
        if (event.defaultPrevented) { return; }  // envoi annulé par une validation
        button.disabled = true;
        button.innerHTML =
          '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' + text;
      }, 0);
    });
  });

  // Retour arrière du navigateur : restaure les boutons.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) { return; }
    forms.forEach(function (form) {
      var button = form.querySelector('button[type="submit"]');
      if (button && button.dataset.originalHtml) {
        button.disabled = false;
        button.innerHTML = button.dataset.originalHtml;
      }
    });
  });
})();
