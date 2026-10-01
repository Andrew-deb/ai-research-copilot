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
  function render(container) {
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

        const label = document.createElement("dt");
        label.textContent = item.description;

        const keys = document.createElement("dd");
        api.format(item.keys).forEach(function (part, index) {
          if (index) { keys.append(" "); }
          const kbd = document.createElement("kbd");
          kbd.textContent = part;
          keys.appendChild(kbd);
        });

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
    note.textContent = "These are fixed for now — they cannot be reassigned.";
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
