/* onboarding.js — narrowing the long field list.

   The only scripted thing on this page, and it is a convenience: thirteen
   further fields behind a disclosure are reachable by scrolling, and this makes
   them reachable by typing. Everything else — the steps, the choices, skipping,
   leaving — is forms and links, because a first-run flow that needs JavaScript
   to be completed is a first-run flow some people cannot complete. */

(function () {
  "use strict";

  var filter = document.querySelector("[data-chip-filter]");
  if (!filter) { return; }

  var chips = Array.prototype.slice.call(
    filter.parentElement.querySelectorAll(".chip"));

  filter.addEventListener("input", function () {
    var needle = filter.value.trim().toLowerCase();

    chips.forEach(function (chip) {
      var label = chip.textContent.trim().toLowerCase();
      // A ticked field always stays visible. Filtering one out of sight while
      // it is still selected hides a choice the person has made and will be
      // submitting.
      var ticked = chip.querySelector("input").checked;
      chip.hidden = !(ticked || !needle || label.indexOf(needle) !== -1);
    });
  });
})();
