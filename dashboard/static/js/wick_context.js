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
  var notice = document.getElementById("wick-asset-status");
  var more = document.getElementById("wick-asset-more");
  var selected = JSON.parse(document.getElementById("wick-context-data").textContent);
  var next = null, generation = 0, controller = null;
  var activeQuery = "", activeKind = "", saveChain = Promise.resolve();
  function references() { return selected.map(function (item) { return { kind: item.kind, id: item.id }; }); }
  function tellShell() {
    if (window.parent !== window) {
      window.parent.postMessage({ source: "alfred-assistant", type: "context-state", value: selected }, location.origin);
    }
  }
  function render() {
    chips.replaceChildren();
    selected.forEach(function (item, index) {
      var button = document.createElement("button");
      button.type = "button"; button.className = "composer-context";
      button.textContent = item.label + (item.available === false ? " (unavailable)" : "") + " ×";
      button.setAttribute("aria-label", "Remove context: " + item.label);
      button.addEventListener("click", function () { selected.splice(index, 1); render(); persist(); });
      chips.appendChild(button);
    });
    tellShell();
  }
  function persist() {
    var id = page.dataset.conversation;
    if (!id) { status.textContent = "Context will be saved with your first message."; return; }
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
          status.textContent = "Context saved.";
        }).catch(function () { status.textContent = "Could not save context. Your next message will retry; keep this page open."; });
    });
  }
  function search(append) {
    if (controller) { controller.abort(); }
    controller = new AbortController(); var current = ++generation;
    if (!append) { results.replaceChildren(); activeQuery = query.value.trim(); activeKind = kind.value; next = null; }
    var params = new URLSearchParams({ q: activeQuery, kind: activeKind });
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
            render(); persist(); notice.textContent = "Added " + item.label + ".";
          }); results.appendChild(button);
        });
        next = body.next_cursor; more.hidden = !next;
        notice.textContent = body.items.length ? "Select an item to add it." : "No matching accessible assets.";
      }).catch(function (error) { if (error.name !== "AbortError") { notice.textContent = "Could not load assets. Try again."; } });
  }
  document.getElementById("wick-add-context").addEventListener("click", function () { dialog.showModal(); search(false); query.focus(); });
  document.getElementById("wick-asset-close").addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("close", function () { if (controller) { controller.abort(); } generation++; });
  document.getElementById("wick-asset-search").addEventListener("click", function () { search(false); });
  query.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); search(false); } });
  more.addEventListener("click", function () { search(true); });
  var mode = document.getElementById("chat-mode");
  if (mode) { mode.addEventListener("change", function () { controls.hidden = mode.value !== "wick"; }); }
  document.addEventListener("wick:conversation", persist);
  window.addEventListener("message", function (event) {
    if (event.origin !== location.origin || event.source !== window.parent || !event.data || event.data.source !== "alfred-shell" || event.data.type !== "context") { return; }
    var item = event.data.value;
    selected = item ? [{ kind: item.id ? item.kind : "page", id: item.id || item.kind, label: item.label }] : [];
    render(); persist();
  });
  window.WickContext = { references: references, ready: function () { return saveChain; } };
  render();
})();
