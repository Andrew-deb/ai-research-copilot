/* Shared shell for the cross-page assistant. The iframe runs the same chat.js
   as /chat; this file owns only panel geometry, page hints and navigation. */
(function () {
  "use strict";
  var panel = document.getElementById("assistant-panel");
  if (!panel) { return; }
  var frame = document.getElementById("assistant-frame");
  var toggle = document.getElementById("assistant-toggle");
  var closeButton = document.getElementById("assistant-close");
  var full = document.getElementById("assistant-full");
  var newButton = document.getElementById("assistant-new");
  var historyButton = document.getElementById("assistant-history");
  var contextLabel = document.getElementById("assistant-context");
  var owner = panel.dataset.owner || "demo";
  var stateKey = "alfred-panel-open:" + owner;
  var threadKey = "alfred-panel-thread:" + owner;
  var conversation = null;
  var lastFocus = null;
  try { conversation = sessionStorage.getItem(threadKey); } catch (e) { /* private mode */ }

  function context() {
    var item = document.querySelector(".paper-detail[data-paper-id], .collection-detail[data-collection-id], .goal-detail[data-goal-id]");
    if (item) {
      var kind = item.dataset.paperId ? "paper" : item.dataset.collectionId ? "collection" : "goal";
      var name = item.querySelector("h1, h2");
      return { kind: kind, id: item.dataset.paperId || item.dataset.collectionId || item.dataset.goalId,
        label: name ? name.textContent.trim() : kind };
    }
    var route = window.location.pathname.split("/")[1];
    var names = { dashboard: "Dashboard", search: "Paper search", collections: "Collections",
      progress: "Reading progress", notes: "Notes", goals: "Learning goals" };
    return names[route] ? { kind: route, id: "", label: names[route] } : null;
  }
  var current = context();
  contextLabel.textContent = current ? "Context: " + current.label : "Research workspace";
  contextLabel.title = contextLabel.textContent;

  function url() {
    var args = new URLSearchParams();
    if (conversation) { args.set("conversation_id", conversation); }
    if (current) {
      args.set("context_kind", current.kind);
      if (current.id) { args.set("context_id", current.id); }
    }
    return "/chat/assistant?" + args.toString();
  }
  function remember(open) {
    try { sessionStorage.setItem(stateKey, open ? "1" : "0"); } catch (e) { /* private mode */ }
  }
  function open() {
    if (window.RCNotesPanel && !window.RCNotesPanel.close()) { return; }
    lastFocus = document.activeElement;
    panel.hidden = false;
    document.body.classList.add("assistant-docked");
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Close Alfred assistant");
    remember(true);
    if (frame.getAttribute("src") === "about:blank") { frame.src = url(); }
    else { frame.contentWindow.postMessage({ source: "alfred-shell", type: "focus" }, location.origin); }
  }
  function close(restoreFocus) {
    if (panel.hidden) { return; }
    panel.hidden = true;
    document.body.classList.remove("assistant-docked");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open Alfred assistant");
    remember(false);
    if (restoreFocus) { (lastFocus && lastFocus.isConnected ? lastFocus : toggle).focus(); }
  }
  toggle.addEventListener("click", function () { panel.hidden ? open() : close(true); });
  closeButton.addEventListener("click", function () { close(true); });
  full.addEventListener("click", function () {
    location.href = conversation ? "/chat/" + encodeURIComponent(conversation) : "/chat";
  });
  newButton.addEventListener("click", function () {
    conversation = null;
    try {
      sessionStorage.removeItem(threadKey);
      sessionStorage.removeItem("alfred-panel-draft:" + owner);
    } catch (e) { /* private mode */ }
    frame.src = url();
  });
  if (historyButton) {
    historyButton.addEventListener("click", function () {
      var opener = document.getElementById("chat-search-open");
      if (opener) { opener.click(); }
    });
    var results = document.getElementById("chat-search-results");
    if (results) {
      results.addEventListener("click", function (event) {
        if (panel.hidden) { return; }
        var link = event.target.closest("a[href]");
        if (!link) { return; }
        var match = new URL(link.href).pathname.match(/^\/chat\/([a-f0-9-]{36})$/i);
        if (!match) { return; }
        event.preventDefault();
        conversation = match[1];
        try { sessionStorage.setItem(threadKey, conversation); } catch (e) { /* private mode */ }
        document.getElementById("chat-search-close").click();
        frame.src = url();
      });
    }
  }
  document.addEventListener("alfred:notes-opening", function () { close(false); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.hidden && !event.defaultPrevented) { close(true); }
  });
  frame.addEventListener("load", function () {
    if (!panel.hidden) { frame.contentWindow.postMessage({ source: "alfred-shell", type: "focus" }, location.origin); }
  });
  window.addEventListener("message", function (event) {
    if (event.origin !== location.origin || event.source !== frame.contentWindow ||
        !event.data || event.data.source !== "alfred-assistant") { return; }
    if (event.data.type === "conversation" && typeof event.data.value === "string") {
      conversation = event.data.value;
      try { sessionStorage.setItem(threadKey, conversation); } catch (e) { /* private mode */ }
    }
    if (event.data.type === "close") { close(true); }
    if (event.data.type === "writes" && Array.isArray(event.data.value) && event.data.value.length) {
      // A tool_end(ok=true) is the only signal for a completed mutation. Wait
      // until the turn ends before reloading, so its final answer can persist.
      location.reload();
    }
  });
  new MutationObserver(function () {
    if (frame.contentWindow && frame.getAttribute("src") !== "about:blank") {
      frame.contentWindow.postMessage({ source: "alfred-shell", type: "theme",
        value: document.documentElement.dataset.theme }, location.origin);
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  document.addEventListener("DOMContentLoaded", function () {
    try { if (sessionStorage.getItem(stateKey) === "1") { open(); } } catch (e) { /* private mode */ }
  });
})();
