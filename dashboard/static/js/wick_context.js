/* Both Wick surfaces share explicit references; only the server resolves content. */
(function () {
  "use strict";
  var controls = document.getElementById("wick-context-controls");
  if (!controls) { return; }
  var page = document.querySelector(".chat-page");
  var editor = window.WickEditor;
  var input = document.getElementById("chat-input");
  var shortcutRange = null, shortcutOpen = false;
  var status = document.getElementById("wick-context-status");
  var dialog = document.getElementById("wick-asset-picker");
  var results = document.getElementById("wick-asset-results");
  var query = document.getElementById("wick-asset-query");
  var kind = document.getElementById("wick-asset-kind");
  kind.value = "";
  var notice = document.getElementById("wick-asset-status");
  var more = document.getElementById("wick-asset-more");
  var selected = JSON.parse(document.getElementById("wick-context-data").textContent);
  var next = null, generation = 0, controller = null;
  var activeQuery = "", activeKind = "", category = "assets", saveChain = Promise.resolve();
  var menu = document.getElementById("wick-context-menu");
  var plus = document.getElementById("wick-add-context");
  var timer = null, inlineBefore = [];
  var selectedMenu = document.getElementById("wick-selected-context");
  function key(item) { return item.kind + ":" + item.id; }
  function references() { return selected.map(function (item) { return { kind: item.kind, id: item.id }; }); }
  function tellShell() {
    if (window.parent !== window) {
      window.parent.postMessage({ source: "alfred-assistant", type: "context-state", value: selected }, location.origin);
    }
  }
  function render() {
    editor.sync(selected);
    inlineBefore = editor.references().map(key);
    selectedMenu.replaceChildren(); selectedMenu.hidden = !selected.length;
    selected.forEach(function (item) {
      var row = document.createElement("button"); row.type = "button"; row.setAttribute("role", "menuitem");
      row.className = "wick-context-remove"; row.textContent = "× " + item.label;
      row.setAttribute("aria-label", "Remove context: " + item.label);
      row.addEventListener("click", function () { selected = selected.filter(function (value) { return key(value) !== key(item); }); render(); persist(); });
      selectedMenu.appendChild(row);
    });
    plus.title = selected.length ? "Add context (" + selected.length + " selected; manage in menu)" : "Add context";
    tellShell();
  }
  function persist() {
    var id = page.dataset.conversation;
    if (!id) { status.textContent = ""; return; }
    var snapshot = references();
    status.textContent = "Saving context…";
    saveChain = saveChain.catch(function () {}).then(function () {
      return fetch("/chat/" + encodeURIComponent(id) + "/context", {
        method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json",
          "X-CSRFToken": document.querySelector('meta[name="csrf-token"]').content },
        body: JSON.stringify({ references: snapshot })
      }).then(function (response) { if (!response.ok) { throw new Error(); } return response.json(); })
        .then(function (body) {
          if (JSON.stringify(snapshot) === JSON.stringify(references())) { selected = body.items; render(); }
          status.textContent = "";
        }).catch(function () { status.textContent = "Could not save context. Your next message will retry; keep this page open."; });
    });
  }
  function search(append) {
    if (controller) { controller.abort(); }
    controller = new AbortController(); var current = ++generation;
    if (!append) { results.replaceChildren(); activeQuery = query.value.trim(); activeKind = kind.value; next = null; }
    var params = new URLSearchParams({ q: activeQuery, kind: activeKind, category: category });
    if (append && next) { params.set("cursor", next); }
    notice.textContent = "Searching…"; more.hidden = true;
    fetch("/chat/assistant/assets?" + params, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (response) { if (!response.ok) { throw new Error(); } return response.json(); })
      .then(function (body) {
        if (current !== generation) { return; }
        body.items.forEach(function (item) {
          var button = document.createElement("button"); button.type = "button"; button.className = "wick-asset-result";
          var label = document.createElement("strong"); label.textContent = item.label;
          var preview = document.createElement("span"); preview.textContent = item.kind + " · " + (item.snippet || "");
          button.append(label, preview);
          button.addEventListener("click", function () {
            var existing = selected.some(function (value) { return value.kind === item.kind && value.id === item.id; });
            if (!existing && selected.length >= 5) { notice.textContent = "Remove an item before adding another (maximum five)."; return; }
            if (!existing) { selected.push({ kind: item.kind, id: item.id, label: item.label }); }
            editor.insert(item, shortcutRange); render(); persist(); closePicker(); input.focus();
          }); results.appendChild(button);
        });
        next = body.next_cursor; more.hidden = !next;
        notice.textContent = body.items.length ? "Select an item to add it." : "No matching accessible assets.";
      }).catch(function (error) { if (error.name !== "AbortError") { notice.textContent = "Could not load assets. Try again."; } });
  }
  function closePicker() {
    shortcutOpen = false; shortcutRange = null;
    editor.element.setAttribute("aria-expanded", "false");
    menu.hidden = true; dialog.hidden = true; plus.setAttribute("aria-expanded", "false");
    if (controller) { controller.abort(); } generation++; clearTimeout(timer);
  }
  function categories() {
    closePicker(); menu.hidden = false; plus.setAttribute("aria-expanded", "true");
    menu.dataset.up = window.innerHeight - plus.getBoundingClientRect().bottom < menu.offsetHeight + 12 ? "true" : "false";
  }
  function chooseCategory(value, shortcut) {
    category = value; menu.hidden = true; dialog.hidden = false;
    plus.setAttribute("aria-expanded", "true");
    document.getElementById("wick-asset-title").textContent = category === "pages" ? "# Pages" : "@ Assets";
    kind.hidden = category === "pages"; kind.value = ""; query.value = "";
    kind.querySelectorAll("button").forEach(function (item) { item.setAttribute("aria-pressed", item.dataset.kind === "" ? "true" : "false"); });
    query.placeholder = category === "pages" ? "Find a page…" : "Find a paper, note, collection or goal…";
    var rect = controls.getBoundingClientRect(), top = formTop();
    dialog.style.bottom = String(rect.bottom - top + 8) + "px";
    dialog.style.maxHeight = String(Math.max(160, top - 16)) + "px";
    search(false); if (!shortcut) { query.focus(); }
  }
  function formTop() { return document.getElementById("chat-composer").getBoundingClientRect().top; }
  controls.addEventListener("pointerdown", function () { editor.remember(); }, true);
  plus.addEventListener("click", function () { menu.hidden && dialog.hidden ? categories() : closePicker(); });
  menu.querySelectorAll("[data-context-category]").forEach(function (button) {
    button.addEventListener("click", function () { chooseCategory(button.dataset.contextCategory); });
  });
  document.getElementById("wick-asset-back").addEventListener("click", function () { categories(); menu.querySelector("button").focus(); });
  document.getElementById("wick-asset-close").addEventListener("click", function () { closePicker(); plus.focus(); });
  query.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { search(false); }, 200); });
  query.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); clearTimeout(timer); search(false); } });
  kind.addEventListener("click", function (event) {
    var button = event.target.closest("[data-kind]"); if (!button) { return; }
    kind.value = button.dataset.kind;
    kind.querySelectorAll("button").forEach(function (item) { item.setAttribute("aria-pressed", item === button ? "true" : "false"); });
    search(false);
  });
  more.addEventListener("click", function () { search(true); });
  document.addEventListener("click", function (event) { if (!controls.contains(event.target)) { closePicker(); } });
  document.addEventListener("keydown", function (event) {
    if ((menu.hidden && dialog.hidden) || event.isComposing || editor.composing()) { return; }
    if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); var wasShortcut = shortcutOpen; closePicker(); if (wasShortcut) { input.focus(); } else { plus.focus(); } }
    if (event.key === "Enter" && shortcutOpen && !event.isComposing && !editor.composing()) {
      event.preventDefault(); event.stopImmediatePropagation(); var first = results.contains(document.activeElement) && document.activeElement.matches("button:not(:disabled)") ? document.activeElement : results.querySelector("button:not(:disabled)"); if (first) { first.click(); } return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      var options = Array.from((menu.hidden ? results : menu).querySelectorAll("button:not(:disabled):not([hidden])"));
      var index = options.indexOf(document.activeElement);
      if (options.length) { event.preventDefault(); options[index < 0 ? (event.key === "ArrowDown" ? 0 : options.length - 1) : (index + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length].focus(); }
    }
  }, true);
  editor.element.setAttribute("aria-controls", "wick-asset-picker");
  editor.element.setAttribute("aria-expanded", "false");
  input.addEventListener("input", function () {
    if (controls.hidden || editor.composing()) { return; }
    var remaining = editor.references();
    var remainingKeys = remaining.map(key);
    var removed = inlineBefore.filter(function (value) { return remainingKeys.indexOf(value) < 0; });
    var nextSelected = selected.filter(function (item) { return removed.indexOf(key(item)) < 0; });
    inlineBefore = remainingKeys;
    if (nextSelected.length !== selected.length) { selected = nextSelected; render(); persist(); }
    var trigger = editor.shortcut();
    if (trigger) {
      if (!shortcutOpen || category !== trigger.category) { chooseCategory(trigger.category, true); }
      shortcutOpen = true; shortcutRange = trigger.range; editor.element.setAttribute("aria-expanded", "true");
      query.value = trigger.query; clearTimeout(timer); timer = setTimeout(function () { search(false); }, 200);
    } else if (shortcutOpen) { closePicker(); }
  });
  document.addEventListener("wick:editor-reset", function () { inlineBefore = editor.references().map(key); });
  var mode = document.getElementById("chat-mode");
  if (mode) { mode.addEventListener("change", function () { controls.hidden = mode.value !== "wick" && !document.getElementById("upload-choose"); closePicker(); }); }
  document.addEventListener("wick:conversation", persist);
  window.addEventListener("message", function (event) {
    if (event.origin !== location.origin || event.source !== window.parent || !event.data || event.data.source !== "alfred-shell" || event.data.type !== "context") { return; }
    var item = event.data.value;
    selected = item ? [{ kind: item.id ? item.kind : "page", id: item.id || item.kind, label: item.label }] : [];
    render(); persist();
  });
  window.WickContext = { references: references, formatPrompt: function (text) { return text; }, ready: function () { return saveChain; } };
  editor.references().forEach(function (item) {
    if (selected.length < 5 && !selected.some(function (value) { return value.kind === item.kind && value.id === item.id; })) { selected.push(item); }
  });
  render();
})();
