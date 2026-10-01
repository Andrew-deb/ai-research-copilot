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

  /* -------------------------------------------------------------- usage -- */

  /*
   * The Analytics tab. Fetched when opened, not with the page: two aggregate
   * queries against a database several hundred milliseconds away, for a view
   * most visits never look at.
   *
   * Drawn as plain elements rather than with a charting library. The whole
   * chart is a row of divs with percentage heights, which needs no dependency,
   * inherits both themes for free, and cannot be the reason a settings page
   * fails to load.
   */
  const analyticsBox = document.querySelector("[data-usage-analytics]");
  if (analyticsBox) {
    let loadedFor = null;     // the window+feature drawn, so a re-click is free

    function number(value) {
      return (value || 0).toLocaleString();
    }

    /* Model costs here are fractions of a cent, and "$0.00" is the one answer
       that is actively wrong: it says free when the true figure is small.
       Below a cent the number gets the digits it needs; above, two decimals,
       because at that point it reads as money and should look like it. */
    function usd(value) {
      const amount = value || 0;
      if (amount === 0) { return "$0"; }
      if (amount < 0.01) { return "$" + amount.toFixed(4); }
      return "$" + amount.toFixed(2);
    }

    function ms(value) {
      if (value == null) { return "—"; }
      return value >= 1000 ? (value / 1000).toFixed(1) + "s" : Math.round(value) + "ms";
    }

    function el(tag, className, text) {
      const node = document.createElement(tag);
      if (className) { node.className = className; }
      if (text != null) { node.textContent = text; }
      return node;
    }

    function draw(data) {
      analyticsBox.replaceChildren();

      if (data.unavailable) {
        analyticsBox.append(el("p", "empty-state",
          "Usage history could not be loaded just now."));
        return;
      }
      if (!data.has_data) {
        // Said in words. A grid of zeros looks identical whether somebody has
        // run nothing or nothing is being recorded, and those are not the same.
        analyticsBox.append(el("p", "empty-state",
          data.feature_label
            ? "No " + data.feature_label + " activity in the last " + data.days + " days."
            : "Nothing recorded in the last " + data.days + " days."));
        return;
      }

      const totals = el("div", "usage-totals");
      [["Requests", number(data.totals.operations)],
       ["Tokens", number(data.totals.tokens)],
       ["Model cost", usd(data.totals.cost_usd)],
       ["Errors", number(data.totals.failures)]].forEach(function (pair) {
        const card = el("div", "usage-total");
        card.append(el("span", "usage-total-value", pair[1]),
                    el("span", "usage-total-label", pair[0]));
        totals.appendChild(card);
      });
      analyticsBox.appendChild(totals);

      if (data.daily && data.daily.length) {
        const chart = el("div", "usage-chart");
        chart.setAttribute("role", "img");
        chart.setAttribute("aria-label",
          "Requests per day over the last " + data.days + " days, peaking at " +
          data.peak + ".");
        data.daily.forEach(function (day) {
          const column = el("div", "usage-chart-col");
          const bar = el("span", "usage-chart-bar");
          // A day with activity never renders as nothing: a zero-height bar and
          // an empty day would be the same picture.
          bar.style.height = (day.operations ? Math.max(day.height, 4) : 0) + "%";
          column.appendChild(bar);
          column.title = day.day + ": " + day.operations +
            (day.operations === 1 ? " request" : " requests");
          chart.appendChild(column);
        });
        analyticsBox.appendChild(chart);
      }

      // Only for requests that actually called a model and came back without a
      // price. A turn that never reached a provider is not unpriced, it is
      // free — and a warning about money that was never at stake is noise.
      if (data.totals.cost_unknown) {
        const n = data.totals.cost_unknown;
        const caveat = el("p", "usage-caveat",
          n === 1
            ? "One request is missing from the cost: the model answered it but "
              + "returned no price."
            : n + " requests are missing from the cost: the model answered them "
              + "but returned no price.");
        analyticsBox.appendChild(caveat);
      }

      const table = el("table", "usage-table");
      const head = el("thead");
      const headRow = el("tr");
      ["Feature", "Requests", "Tokens", "Cost", "Median", "95th", "Errors"]
        .forEach(function (label) { headRow.appendChild(el("th", null, label)); });
      head.appendChild(headRow);
      table.appendChild(head);

      const tbody = el("tbody");
      data.features.forEach(function (feature) {
        const row = el("tr");
        row.append(
          el("th", null, feature.label),
          el("td", null, number(feature.operations)),
          el("td", null, number(feature.tokens)),
          el("td", null, usd(feature.cost_usd)),
          el("td", null, ms(feature.median_ms)),
          el("td", null, ms(feature.p95_ms)),
          el("td", feature.failures ? "usage-errors" : null,
             feature.failures ? feature.failures + " (" + feature.failure_rate + "%)" : "—")
        );
        tbody.appendChild(row);
      });
      table.appendChild(tbody);
      analyticsBox.appendChild(table);
    }

    async function load(days, feature) {
      const key = days + ":" + (feature || "");
      if (loadedFor === key) { return; }
      analyticsBox.replaceChildren(el("p", "empty-state", "Loading…"));
      try {
        const response = await fetch(
          "/settings/usage?days=" + days + (feature ? "&feature=" + feature : ""), {
          headers: { "X-Requested-With": "XMLHttpRequest" }
        });
        if (!response.ok) { throw new Error("Request failed"); }
        draw(await response.json());
        loadedFor = key;
      } catch (e) {
        loadedFor = null;     // so the next open tries again rather than sulking
        analyticsBox.replaceChildren(
          el("p", "empty-state", "Usage history could not be loaded just now."));
      }
    }

    document.querySelectorAll("[data-usage-tab]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        const name = tab.dataset.usageTab;
        document.querySelectorAll("[data-usage-view]").forEach(function (view) {
          view.hidden = view.dataset.usageView !== name;
        });
        document.querySelectorAll("[data-usage-tab]").forEach(function (each) {
          const on = each === tab;
          each.classList.toggle("is-selected", on);
          each.setAttribute("aria-selected", on ? "true" : "false");
        });
        if (name === "analytics") { load(currentDays(), currentFeature()); }
      });
    });

    function currentDays() {
      const active = document.querySelector("[data-usage-days].is-selected");
      return parseInt(active ? active.dataset.usageDays : "7", 10);
    }

    const featurePicker = document.querySelector("[data-dropdown]");
    const featureToggle = featurePicker && featurePicker.querySelector("[data-dropdown-toggle]");
    const featureMenu = featurePicker && featurePicker.querySelector(".dropdown-menu");

    function currentFeature() {
      return featureToggle ? featureToggle.value : "";
    }

    document.querySelectorAll("[data-usage-days]").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll("[data-usage-days]").forEach(function (each) {
          each.classList.toggle("is-selected", each === button);
        });
        load(parseInt(button.dataset.usageDays, 10), currentFeature());
      });
    });

    if (featurePicker) {
      function setFeatureMenu(open) {
        featureMenu.hidden = !open;
        featureToggle.setAttribute("aria-expanded", open ? "true" : "false");
      }

      featureToggle.addEventListener("click", function (event) {
        event.stopPropagation();
        setFeatureMenu(featureMenu.hidden);
      });

      featureMenu.addEventListener("click", function (event) {
        const option = event.target.closest("[role=option]");
        if (!option) { return; }

        featureToggle.value = option.dataset.usageFeature;
        featurePicker.querySelector(".dropdown-value").textContent = option.textContent;
        featureMenu.querySelectorAll("[role=option]").forEach(function (each) {
          each.setAttribute("aria-selected", each === option ? "true" : "false");
        });
        setFeatureMenu(false);
        featureToggle.focus();
        load(currentDays(), featureToggle.value);
      });

      document.addEventListener("click", function (event) {
        if (!featurePicker.contains(event.target)) { setFeatureMenu(false); }
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !featureMenu.hidden) {
          setFeatureMenu(false);
          featureToggle.focus();
        }
      });
    }
  }

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
