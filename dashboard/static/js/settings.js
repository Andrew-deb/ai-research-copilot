/*
 * settings.js — section switching, appearance, and the delete confirmation.
 *
 * Appearance is written to localStorage only. None of it reaches the server:
 * a laptop in a bright room and a phone at night want different answers, and
 * syncing them means one device overrules the other.
 *
 * Every storage access is wrapped, because localStorage throws in a private
 * window rather than returning null, and an appearance preference is not worth
 * a page that fails to load.
 */
(function () {
  "use strict";

  const root = document.documentElement;

  function read(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  /* ---------------------------------------------------------------- nav -- */

  const shell = document.querySelector("[data-settings]");
  if (shell) {
    const tabs = shell.querySelectorAll("[data-settings-tab]");
    const panels = shell.querySelectorAll("[data-settings-panel]");

    function show(name) {
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.settingsPanel !== name;
      });
      tabs.forEach((tab) => {
        const active = tab.dataset.settingsTab === name;
        tab.setAttribute("aria-current", active ? "page" : "false");
      });
      // Replace rather than push: a section is where you are on this page, not
      // a place to go Back to. Otherwise leaving Settings means pressing Back
      // once per section you looked at.
      try {
        const url = new URL(window.location.href);
        url.searchParams.set("section", name);
        history.replaceState(null, "", url);
      } catch (e) { /* older browsers */ }
    }

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => show(tab.dataset.settingsTab));
    });

    const current = shell.querySelector('[data-settings-tab][aria-current="page"]');
    show(current ? current.dataset.settingsTab : "preferences");
  }

  /* --------------------------------------------------------------- mode -- */

  const MODE_KEY = "rc-theme";
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

  function applyMode(mode) {
    // Resolved to a concrete value, the same way the no-flash script in the
    // page head does. Removing the attribute instead would leave this page
    // deciding by media query while every other page decided by attribute —
    // the same answer most of the time, and a visible flip when they disagree.
    // The CHOICE stays "system" in storage; only its result is written here.
    const resolved = mode === "system"
      ? (systemDark.matches ? "dark" : "light")
      : mode;
    root.setAttribute("data-theme", resolved);
    document.querySelectorAll("[data-mode]").forEach((button) => {
      const active = button.dataset.mode === mode;
      button.setAttribute("aria-checked", active ? "true" : "false");
      button.classList.toggle("is-selected", active);
    });
  }

  applyMode(read(MODE_KEY, "system"));
  systemDark.addEventListener("change", () => {
    if (read(MODE_KEY, "system") === "system") { applyMode("system"); }
  });

  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      write(MODE_KEY, button.dataset.mode);
      applyMode(button.dataset.mode);
    });
  });

  /* ---------------------------------------------------------- text size -- */

  const FONT_KEY = "rc-font-size";
  // The design's own base is 14px; the range is what stays legible either side
  // of it without the layout coming apart.
  const FONT_MIN = 12;
  const FONT_MAX = 19;
  const FONT_DEFAULT = 14;

  function applyFont(size) {
    root.style.setProperty("--base-font-size", size + "px");
    const output = document.querySelector("[data-font-value]");
    if (output) { output.textContent = size; }
  }

  function currentFont() {
    const stored = parseInt(read(FONT_KEY, ""), 10);
    return Number.isFinite(stored) ? stored : FONT_DEFAULT;
  }

  applyFont(currentFont());

  document.querySelectorAll("[data-font-step]").forEach((button) => {
    button.addEventListener("click", () => {
      const step = parseInt(button.dataset.fontStep, 10);
      const next = Math.min(FONT_MAX, Math.max(FONT_MIN, currentFont() + step));
      write(FONT_KEY, String(next));
      applyFont(next);
    });
  });

  /* ------------------------------------------------------------- motion -- */

  const MOTION_KEY = "rc-motion";

  function applyMotion(choice) {
    // "system" removes the attribute so the stylesheet's prefers-reduced-motion
    // query decides, which is the setting the operating system already holds.
    if (choice === "system") {
      root.removeAttribute("data-motion");
    } else {
      root.setAttribute("data-motion", choice);
    }
    // Scoped to buttons: `root` itself carries data-motion once a choice is
    // applied, and a bare attribute selector would try to style the <html>.
    document.querySelectorAll("button[data-motion]").forEach((button) => {
      const active = button.dataset.motion === choice;
      button.setAttribute("aria-checked", active ? "true" : "false");
      button.classList.toggle("is-selected", active);
    });
  }

  applyMotion(read(MOTION_KEY, "system"));

  document.querySelectorAll("button[data-motion]").forEach((button) => {
    button.addEventListener("click", () => {
      write(MOTION_KEY, button.dataset.motion);
      applyMotion(button.dataset.motion);
    });
  });

  /* ------------------------------------------------------ confirm first -- */

  /*
   * Any form carrying data-confirm asks before it submits.
   *
   * Written once and driven by the attribute rather than wired per button,
   * because the thing that makes these dangerous is that they look identical:
   * four "Sign out" buttons in a list, one of which ends the session you are
   * reading the page in and one of which ends every session you have. The
   * dialog is where they stop looking identical.
   */
  const confirmDialog = document.getElementById("confirm-dialog");
  if (confirmDialog) {
    const message = confirmDialog.querySelector("[data-confirm-message]");
    const proceed = confirmDialog.querySelector("[data-confirm-proceed]");
    const cancel = confirmDialog.querySelector("[data-confirm-cancel]");

    let pending = null;     // what the dialog is currently asking about
    let approved = null;    // what the person has just said yes to

    function open(form) {
      pending = form;
      message.textContent = form.dataset.confirm;
      proceed.textContent = form.dataset.confirmAction || "Continue";

      if (typeof confirmDialog.showModal === "function") { confirmDialog.showModal(); }
      else { confirmDialog.setAttribute("open", ""); }

      // Focus lands on Cancel, not on the action: the safe option should be the
      // one an accidental Enter picks.
      if (cancel) { cancel.focus(); }
    }

    function close() {
      if (typeof confirmDialog.close === "function") { confirmDialog.close(); }
      else { confirmDialog.removeAttribute("open"); }
      pending = null;
    }

    document.addEventListener("submit", (event) => {
      const form = event.target.closest("form[data-confirm]");
      if (!form) { return; }

      // The second pass, after a yes. Cleared immediately so a later submit of
      // the same form has to be confirmed again.
      if (form === approved) { approved = null; return; }

      event.preventDefault();
      open(form);
    }, true);

    if (proceed) {
      proceed.addEventListener("click", () => {
        const form = pending;
        close();
        if (!form) { return; }

        // Set AFTER close(), which clears `pending` — reusing that one variable
        // meant the resubmit arrived with it already null and re-opened the
        // dialog it had just answered.
        approved = form;
        if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
      });
    }

    if (cancel) { cancel.addEventListener("click", close); }
    confirmDialog.addEventListener("close", () => { pending = null; });
  }

  /* ------------------------------------------------------------- delete -- */

  const dialog = document.getElementById("delete-account-dialog");
  if (dialog) {
    const open = document.querySelector("[data-delete-open]");
    const cancel = dialog.querySelector("[data-delete-cancel]");
    const confirm = dialog.querySelector("[data-delete-confirm]");
    const submit = dialog.querySelector("[data-delete-submit]");

    if (open) {
      open.addEventListener("click", () => {
        if (confirm) { confirm.value = ""; }
        if (submit) { submit.disabled = true; }
        if (typeof dialog.showModal === "function") { dialog.showModal(); }
        else { dialog.setAttribute("open", ""); }
      });
    }

    if (cancel) {
      cancel.addEventListener("click", () => {
        if (typeof dialog.close === "function") { dialog.close(); }
        else { dialog.removeAttribute("open"); }
      });
    }

    // The button unlocks only when the typed address matches. Case-insensitive,
    // because an email address is, and refusing "Andy@" for "andy@" would be
    // this page inventing a rule the rest of the system does not have.
    if (confirm && submit) {
      confirm.addEventListener("input", () => {
        const expected = (confirm.dataset.expect || "").trim().toLowerCase();
        submit.disabled = confirm.value.trim().toLowerCase() !== expected;
      });
    }
  }
})();
