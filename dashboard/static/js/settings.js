/* settings.js — the two appearance controls.

   They live here rather than on the account because they are per-DEVICE: a
   laptop in a bright room and a phone at night want different answers, and
   syncing them means one device silently overrules the other. Both just drive
   the same browser storage the topbar toggle and the rails already use, so
   there is one source of truth per setting rather than two that can disagree. */

(function () {
  "use strict";

  var theme = document.getElementById("settings-theme");
  if (theme) {
    theme.addEventListener("click", function () {
      // Delegated to the topbar control rather than reimplemented: one place
      // decides what "switch theme" means, and it already handles storage.
      var toggle = document.getElementById("theme-toggle");
      if (toggle) { toggle.click(); }
    });
  }

  var reset = document.getElementById("settings-reset-layout");
  if (reset) {
    reset.addEventListener("click", function () {
      ["rc-rail-sidebar", "rc-rail-notes"].forEach(function (key) {
        try { window.localStorage.removeItem(key); } catch (e) { /* private mode */ }
      });
      document.documentElement.style.removeProperty("--sidebar-w");
      document.documentElement.style.removeProperty("--notes-w");

      if (window.RC) { window.RC.toast("Sidebar widths reset.", "success"); }
    });
  }
})();
