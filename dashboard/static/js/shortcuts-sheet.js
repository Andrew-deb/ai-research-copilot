/*
 * shortcuts-sheet.js — the list, built from the registry.
 *
 * Generated rather than written, because a hand-maintained list of shortcuts is
 * a list that is wrong. Nothing here knows what any key does; it reads
 * RCShortcuts.REGISTRY and renders it, so adding a shortcut documents it.
 *
 * Built once, on first open. Most visits never press Mod+/ and there is no
 * reason for every page load to construct a dialog nobody asked for.
 */
(function () {
  "use strict";

  const api = window.RCShortcuts;
  if (!api) { return; }

  let dialog = null;

  /* Renders the registry into any container. Used twice — the Keyboard section
     of Settings, which shows the list outright, and the dialog that Mod+/ opens
     from anywhere. One renderer, because two would be two lists to keep in
     step, which is the problem this whole file exists to avoid. */
  function keycaps(into, keys) {
    into.replaceChildren();
    api.format(keys).forEach(function (part, index) {
      if (index) { into.append(" "); }
      const kbd = document.createElement("kbd");
      kbd.textContent = part;
      into.appendChild(kbd);
    });
  }

  /*
   * Capture one chord.
   *
   * The modifier keys are ignored while held: a listener that accepted the
   * first keydown would record "Control" the instant somebody reached for
   * Ctrl+J, before they had finished pressing it.
   *
   * Escape cancels rather than binding, because a capture you cannot back out
   * of is a trap — and Escape is not assignable anyway.
   */
  function capture(row, item, keysCell, onDone) {
    const MODIFIERS = ["Control", "Meta", "Shift", "Alt"];
    row.classList.add("is-capturing");
    keysCell.replaceChildren(document.createTextNode("Press a key combination…"));

    function finish() {
      document.removeEventListener("keydown", onKey, true);
      row.classList.remove("is-capturing");
      onDone();
    }

    function onKey(event) {
      event.preventDefault();
      event.stopPropagation();

      if (event.key === "Escape") { return finish(); }
      if (MODIFIERS.indexOf(event.key) !== -1) { return; }

      const parts = [];
      if (event.metaKey || event.ctrlKey) { parts.push("Mod"); }
      if (event.shiftKey) { parts.push("Shift"); }
      if (event.altKey) { parts.push("Alt"); }
      parts.push(event.key.length === 1 ? event.key.toUpperCase() : event.key);

      // A bare letter would fire while somebody is typing it into the page.
      if (!parts.length || (parts.length === 1 && event.key.length === 1)) {
        say(row, "Use a combination that includes Ctrl, Cmd or Alt.");
        return finish();
      }

      const result = api.setBinding(item.id, parts.join("+"));
      if (!result.ok) {
        say(row, result.reason === "conflict"
          ? "That is already " + result.conflict.description.toLowerCase() + "."
          : "That shortcut cannot be changed.");
      } else {
        say(row, "");
      }
      finish();
    }

    document.addEventListener("keydown", onKey, true);
  }

  function say(row, message) {
    let note = row.querySelector(".shortcut-note");
    if (!message) { if (note) { note.remove(); } return; }
    if (!note) {
      note = document.createElement("span");
      note.className = "shortcut-note";
      row.querySelector("dt").appendChild(note);
    }
    note.textContent = message;
  }

  function render(container) {
    container.replaceChildren();

    // Grouped in the order the registry declares them, so the list reads the
    // way somebody works rather than alphabetically.
    const groups = [];
    api.REGISTRY.forEach(function (item) {
      let group = groups.filter(function (g) { return g.name === item.group; })[0];
      if (!group) { group = { name: item.group, items: [] }; groups.push(group); }
      group.items.push(item);
    });

    groups.forEach(function (group) {
      const title = document.createElement("h3");
      title.textContent = group.name;
      container.appendChild(title);

      const list = document.createElement("dl");
      list.className = "shortcut-list";

      group.items.forEach(function (item) {
        const row = document.createElement("div");
        if (api.isCustom(item.id)) { row.classList.add("is-custom"); }

        const label = document.createElement("dt");
        label.append(document.createTextNode(item.description));

        const keys = document.createElement("dd");
        keycaps(keys, api.keysFor(item.id));

        if (item.assignable) {
          const change = document.createElement("button");
          change.type = "button";
          change.className = "shortcut-change";
          change.textContent = "Change";
          change.addEventListener("click", function () {
            capture(row, item, keys, function () { render(container); });
          });
          keys.appendChild(change);

          if (api.isCustom(item.id)) {
            const undo = document.createElement("button");
            undo.type = "button";
            undo.className = "shortcut-change";
            undo.textContent = "Reset";
            undo.addEventListener("click", function () {
              api.resetBinding(item.id);
              render(container);
            });
            keys.appendChild(undo);
          }
        } else {
          // Said on the row rather than as a blanket note at the top, which
          // claimed every shortcut was fixed once some of them were not.
          const fixed = document.createElement("span");
          fixed.className = "shortcut-fixed";
          fixed.textContent = "Fixed";
          keys.appendChild(fixed);
        }

        row.append(label, keys);
        list.appendChild(row);
      });

      container.appendChild(list);
    });
  }

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "modal shortcuts-sheet";
    dialog.id = "shortcuts-sheet";

    const heading = document.createElement("h2");
    heading.textContent = "Keyboard shortcuts";
    dialog.appendChild(heading);

    const note = document.createElement("p");
    note.className = "settings-sub";
    note.textContent = "Change any of these in Settings → Keyboard.";
    dialog.appendChild(note);

    render(dialog);

    const actions = document.createElement("div");
    actions.className = "modal-actions";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn-ghost btn-sm";
    close.textContent = "Close";
    close.addEventListener("click", function () { dialog.close(); });
    actions.appendChild(close);
    dialog.appendChild(actions);

    document.body.appendChild(dialog);
  }

  // Settings shows the list outright rather than behind a button. Pressing a
  // button to reveal a reference list, on a page whose whole subject is that
  // list, is a click that buys nothing.
  const inline = document.querySelector("[data-shortcuts-list]");
  if (inline) { render(inline); }

  const resetAll = document.querySelector("[data-shortcuts-reset]");
  if (resetAll && inline) {
    resetAll.addEventListener("click", function () {
      api.resetAll();
      render(inline);
    });
  }

  function toggle() {
    if (!dialog) { build(); }
    if (dialog.open) { dialog.close(); }
    else if (typeof dialog.showModal === "function") { dialog.showModal(); }
    else { dialog.setAttribute("open", ""); }
  }

  api.register("help.shortcuts", toggle);

  // Anything with this attribute opens the sheet — the settings row does, and
  // so can anything added later, without this file knowing about it.
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-shortcuts-open]")) {
      event.preventDefault();
      toggle();
    }
  });
})();
