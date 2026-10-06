/* Page de saisie du code OTP : chiffres uniquement + compte à rebours du renvoi. */
(function () {
  "use strict";

  var input = document.querySelector(".otp-input");
  if (input) {
    input.addEventListener("input", function () {
      var max = parseInt(input.getAttribute("maxlength"), 10) || 6;
      input.value = input.value.replace(/\D/g, "").slice(0, max);
    });
  }

  // Le serveur applique de toute façon le délai : ceci n'est qu'un confort.
  var button = document.getElementById("resend-btn");
  if (!button) { return; }
  var remaining = parseInt(button.dataset.remaining, 10) || 0;
  var label = button.querySelector("[data-count]");

  function tick() {
    if (remaining > 0) {
      button.disabled = true;
      label.textContent = "(" + remaining + " s)";
      remaining -= 1;
      setTimeout(tick, 1000);
    } else {
      button.disabled = false;
      label.textContent = "";
    }
  }
  tick();
})();
