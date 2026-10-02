/* Both Wick surfaces share explicit references; only the server resolves content. */
(function () {
  "use strict";
  var controls = document.getElementById("wick-context-controls");
  if (!controls) { return; }
  var page = document.querySelector(".chat-page");
  var chips = document.getElementById("wick-context-chips");
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
  var timer = null;
  function references() { return selected.map(function (item) { return { kind: item.kind, id: item.id }; }); }
  function tellShell() {
    if (window.parent !== window) {
      window.parent.postMessage({ source: "alfred-assistant", type: "context-state", value: selected }, location.origin);
    }
  }
  function render() {
    chips.replaceChildren();
    selected.forEach(function (item, index) {
      var wrapper = document.createElement("span"); wrapper.className = "wick-reference";
      var destination = window.WickMentions.href(item);
      var label = document.createElement(destination ? "a" : "span");
      label.textContent = (item.kind === "page" ? "▤ " : "@") + item.label + (item.available === false ? " (unavailable)" : "");
      if (destination) { label.href = destination; label.target = "_blank"; label.rel = "noopener"; }
      var remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×";
      remove.setAttribute("aria-label", "Remove context: " + item.label);
      remove.addEventListener("click", function () { selected.splice(index, 1); render(); persist(); });
      wrapper.append(label, remove); chips.appendChild(wrapper);
    });
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
          button.disabled = selected.some(function (existing) { return existing.kind === item.kind && existing.id === item.id; });
          button.addEventListener("click", function () {
            if (selected.length >= 5) { notice.textContent = "Remove an item before adding another (maximum five)."; return; }
            selected.push({ kind: item.kind, id: item.id, label: item.label }); button.disabled = true;
            render(); persist(); closePicker(); document.getElementById("chat-input").focus();
          }); results.appendChild(button);
        });
        next = body.next_cursor; more.hidden = !next;
        notice.textContent = body.items.length ? "Select an item to add it." : "No matching accessible assets.";
      }).catch(function (error) { if (error.name !== "AbortError") { notice.textContent = "Could not load assets. Try again."; } });
  }
  function closePicker() {
    menu.hidden = true; dialog.hidden = true; plus.setAttribute("aria-expanded", "false");
    if (controller) { controller.abort(); } generation++; clearTimeout(timer);
  }
  function categories() {
    closePicker(); menu.hidden = false; plus.setAttribute("aria-expanded", "true");
    menu.dataset.up = window.innerHeight - plus.getBoundingClientRect().bottom < 130 ? "true" : "false";
  }
  function chooseCategory(value) {
    category = value; menu.hidden = true; dialog.hidden = false;
    plus.setAttribute("aria-expanded", "true");
    document.getElementById("wick-asset-title").textContent = category === "pages" ? "▤ Pages" : "@ Assets";
    kind.hidden = category === "pages"; kind.value = ""; query.value = "";
    kind.querySelectorAll("button").forEach(function (item) { item.setAttribute("aria-pressed", item.dataset.kind === "" ? "true" : "false"); });
    query.placeholder = category === "pages" ? "Find a page…" : "Find a paper, note, collection or goal…";
    var rect = controls.getBoundingClientRect(), top = formTop();
    dialog.style.bottom = String(rect.bottom - top + 8) + "px";
    dialog.style.maxHeight = String(Math.max(160, top - 16)) + "px";
    search(false); query.focus();
  }
  function formTop() { return document.getElementById("chat-composer").getBoundingClientRect().top; }
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
    if (menu.hidden && dialog.hidden) { return; }
    if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); closePicker(); plus.focus(); }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      var options = Array.from((menu.hidden ? results : menu).querySelectorAll("button:not(:disabled)"));
      var index = options.indexOf(document.activeElement);
      if (options.length) { event.preventDefault(); options[index < 0 ? (event.key === "ArrowDown" ? 0 : options.length - 1) : (index + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length].focus(); }
    }
  }, true);
  var mode = document.getElementById("chat-mode");
  if (mode) { mode.addEventListener("change", function () { controls.hidden = mode.value !== "wick"; chips.hidden = controls.hidden; closePicker(); }); }
  document.addEventListener("wick:conversation", persist);
  window.addEventListener("message", function (event) {
    if (event.origin !== location.origin || event.source !== window.parent || !event.data || event.data.source !== "alfred-shell" || event.data.type !== "context") { return; }
    var item = event.data.value;
    selected = item ? [{ kind: item.id ? item.kind : "page", id: item.id || item.kind, label: item.label }] : [];
    render(); persist();
  });
  window.WickContext = { references: references, formatPrompt: function (text) { return window.WickMentions.format(text, selected); }, ready: function () { return saveChain; } };
  render();
})();
