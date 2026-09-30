/*
 * tag-input.js — add entries one at a time.
 *
 * Replaces a row of fixed empty boxes. Each committed entry becomes a list item
 * carrying its own hidden input, so the form posts the same field name it always
 * did and removing an entry takes its value with it — no index juggling, no gaps
 * where a deleted middle row used to be.
 *
 * Enter commits. That needs the keypress cancelled: in a form with one text
 * input, Enter submits, so without preventDefault the first topic somebody typed
 * would save a half-filled form instead of being added to the list.
 */
(function () {
  "use strict";

  function setup(root) {
    const list = root.querySelector("[data-tag-list]");
    const entry = root.querySelector("[data-tag-entry]");
    const commit = root.querySelector("[data-tag-commit]");
    const hint = root.querySelector("[data-tag-hint]");
    const name = root.dataset.name;
    const max = parseInt(root.dataset.max, 10) || 5;

    if (!list || !entry) { return; }

    function values() {
      return Array.from(list.querySelectorAll("input[type=hidden]"))
        .map((input) => input.value);
    }

    function refresh() {
      const count = values().length;
      const full = count >= max;

      list.hidden = count === 0;
      entry.disabled = full;
      if (commit) { commit.disabled = full; }
      if (hint) {
        hint.textContent = full
          ? `That is the maximum of ${max}. Remove one to add another.`
          : `Press Enter to add. Up to ${max}.`;
      }
    }

    function add(text) {
      const value = (text || "").trim();
      if (!value) { return; }

      // Silently ignored rather than warned about: re-typing something you
      // already listed is a slip, and an error message for it is noise.
      if (values().some((v) => v.toLowerCase() === value.toLowerCase())) {
        entry.value = "";
        return;
      }
      if (values().length >= max) { return; }

      const item = document.createElement("li");
      item.className = "tag-item";

      const label = document.createElement("span");
      label.className = "tag-text";
      label.textContent = value;

      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = name;
      hidden.value = value;

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-btn tag-remove";
      remove.setAttribute("data-tag-remove", "");
      remove.setAttribute("aria-label", `Remove ${value}`);
      remove.innerHTML =
        '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" ' +
        'aria-hidden="true"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>' +
        '<path d="M10 11v6M14 11v6"/></svg>';

      item.append(label, hidden, remove);
      list.appendChild(item);

      entry.value = "";
      refresh();
    }

    entry.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") { return; }
      event.preventDefault();          // otherwise this submits the form
      add(entry.value);
    });

    if (commit) {
      commit.addEventListener("click", () => { add(entry.value); entry.focus(); });
    }

    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-tag-remove]");
      if (!button) { return; }
      button.closest(".tag-item").remove();
      refresh();
      entry.focus();
    });

    // Anything typed and not committed still counts. Losing a topic because
    // somebody hit Save instead of Enter would be the widget quietly discarding
    // their answer.
    const form = root.closest("form");
    if (form) {
      form.addEventListener("submit", () => { add(entry.value); });
    }

    refresh();
  }

  document.querySelectorAll("[data-tag-input]").forEach(setup);
})();
