/* main.js — shared helpers: flash dismissal, JSON POST, toasts, goal-match panels. */

(function () {
  "use strict";

  // ---------- Flash messages ----------
  document.addEventListener("click", function (e) {
    if (e.target.classList.contains("flash-close")) {
      e.target.closest(".flash").remove();
    }
  });

  // ---------- Confirm before something irreversible ----------
  // Delegated and attribute-driven, so a form only has to say what it is about
  // to do. Without JavaScript the form still submits — the confirmation is a
  // courtesy, not the safeguard; the safeguard is that it is a POST scoped to
  // the owner.
  document.addEventListener("submit", function (e) {
    const form = e.target.closest("[data-confirm]");
    if (form && !window.confirm(form.getAttribute("data-confirm"))) {
      e.preventDefault();
    }
  });

  // ---------- Theme toggle ----------
  const themeToggle = document.getElementById("theme-toggle");
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("rc-theme", next); } catch (e) { /* private mode */ }
    });
  }

  // Account actions stay behind the profile control in both sidebar states.
  const profileToggle = document.getElementById("profile-toggle");
  const profileMenu = document.getElementById("profile-menu");
  const logoutOpen = document.getElementById("logout-open");
  const logoutDialog = document.getElementById("logout-dialog");
  const logoutCancel = document.getElementById("logout-cancel");
  function closeProfile(restoreFocus) {
    if (!profileMenu || profileMenu.hidden) { return; }
    profileMenu.hidden = true;
    profileToggle.setAttribute("aria-expanded", "false");
    if (restoreFocus) { profileToggle.focus(); }
  }
  if (profileToggle && profileMenu) {
    profileToggle.addEventListener("click", function () {
      const opening = profileMenu.hidden;
      profileMenu.hidden = !opening;
      profileToggle.setAttribute("aria-expanded", String(opening));
      if (opening && logoutOpen) { logoutOpen.focus(); }
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".profile-actions")) { closeProfile(false); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !profileMenu.hidden) {
        closeProfile(true);
        e.stopPropagation();
      }
    }, true);
  }
  if (logoutOpen && logoutDialog) {
    logoutOpen.addEventListener("click", function () {
      closeProfile(false);
      logoutDialog.showModal();
      if (logoutCancel) { logoutCancel.focus(); }
    });
  }
  if (logoutCancel && logoutDialog) {
    logoutCancel.addEventListener("click", function () { logoutDialog.close(); });
    logoutDialog.addEventListener("close", function () { if (profileToggle) { profileToggle.focus(); } });
    logoutDialog.addEventListener("click", function (e) {
      if (e.target === logoutDialog) { logoutDialog.close(); }
    });
  }

  // Desktop preference and mobile drawer state are independent.
  const sidebar = document.getElementById("sidebar");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const scrim = document.getElementById("sidebar-scrim");
  const shell = document.querySelector(".app-shell");
  const collapseBtn = document.getElementById("sidebar-collapse");
  const mobile = window.matchMedia("(max-width: 900px)");
  const COLLAPSE_KEY = "rc-sidebar-collapsed";

  function syncNavigation() {
    if (!sidebar || !shell) { return; }
    const open = sidebar.classList.contains("open");
    const collapsed = shell.classList.contains("sidebar-collapsed");
    sidebar.inert = mobile.matches && !open;
    if (scrim) { scrim.hidden = !mobile.matches || !open; }
    const main = document.querySelector(".main-col");
    if (main) { main.inert = mobile.matches && open; }
    document.body.classList.toggle("navigation-open", mobile.matches && open);
    if (sidebarToggle) { sidebarToggle.setAttribute("aria-expanded", String(mobile.matches ? open : !collapsed)); }
    if (collapseBtn) {
      const label = mobile.matches ? "Close navigation" : (collapsed ? "Expand navigation" : "Collapse navigation");
      collapseBtn.setAttribute("aria-label", label);
      collapseBtn.title = label;
      collapseBtn.setAttribute("aria-expanded", String(mobile.matches ? open : !collapsed));
    }
  }
  function closeSidebar(restoreFocus) {
    if (!sidebar) { return; }
    const wasOpen = sidebar.classList.contains("open");
    sidebar.classList.remove("open");
    syncNavigation();
    if (wasOpen && restoreFocus && sidebarToggle) { sidebarToggle.focus(); }
  }
  function setCollapsed(collapsed) {
    if (!shell) { return; }
    shell.classList.toggle("sidebar-collapsed", collapsed);
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (e) {}
    syncNavigation();
  }
  try {
    if (shell) { shell.classList.toggle("sidebar-collapsed", localStorage.getItem(COLLAPSE_KEY) === "1"); }
  } catch (e) {}
  syncNavigation();
  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener("click", function () {
      if (!mobile.matches) { setCollapsed(false); return; }
      sidebar.classList.add("open");
      syncNavigation();
      if (collapseBtn) { collapseBtn.focus(); }
    });
  }
  if (collapseBtn && shell) {
    collapseBtn.addEventListener("click", function () {
      if (mobile.matches) { closeSidebar(true); }
      else { setCollapsed(!shell.classList.contains("sidebar-collapsed")); }
    });
  }
  if (scrim) { scrim.addEventListener("click", function () { closeSidebar(true); }); }
  mobile.addEventListener("change", function () {
    const focusWasInSidebar = sidebar && sidebar.contains(document.activeElement);
    closeSidebar(false);
    if (mobile.matches && focusWasInSidebar && sidebarToggle) { sidebarToggle.focus(); }
  });
  // Chat history search is a filter over one list, so it takes the narrower
  // chord; the global palette keeps Ctrl+K, which is what people arrive
  // expecting. Both used to be bound here and in palette.js independently, and
  // both answered K — this one winning purely by being registered first.
  if (window.RCShortcuts) {
    window.RCShortcuts.register("chat.search", function () {
      const opener = document.getElementById("chat-search-open");
      if (opener && !opener.closest("[inert]")) { opener.click(); }
    });
  }

  document.addEventListener("keydown", function (e) {
    const dialog = document.getElementById("chat-search-scrim");
    if (e.key === "Escape") {
      if (dialog && !dialog.hidden) { closeChatSearch(); return; }
      closeSidebar(true);
    }
    if (e.key === "Tab" && mobile.matches && sidebar && sidebar.classList.contains("open") && (!dialog || dialog.hidden)) {
      const items = Array.from(sidebar.querySelectorAll('a[href], button, [tabindex="0"]')).filter(function (el) { return el.getClientRects().length && !el.disabled; });
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  // ---------- Chat history search dialog ----------
  // Filters conversation titles by keyword. Deliberately NOT the corpus search:
  // that one runs semantic retrieval over papers and already has its own page.
  const searchScrim = document.getElementById("chat-search-scrim");
  const searchOpen = document.getElementById("chat-search-open");
  const searchClose = document.getElementById("chat-search-close");
  const searchInput = document.getElementById("chat-search-input");
  const searchResults = document.getElementById("chat-search-results");
  const searchNone = document.getElementById("chat-search-none");
  const searchMore = document.getElementById("chat-search-more");
  const searchKinds = searchScrim ? searchScrim.querySelectorAll("[data-search-kind]") : [];
  let historyKind = "all";
  let historyOffset = 0;
  let searchRequest = 0;

  async function loadHistory(append = false) {
    if (!searchResults) { return; }
    const serial = ++searchRequest;
    const offset = append ? historyOffset : 0;
    const query = searchInput ? searchInput.value.trim() : "";
    try {
      const response = await fetch("/chat/history?" + new URLSearchParams({
        kind: historyKind, q: query, limit: "30", offset: String(offset),
      }));
      if (!response.ok) { throw new Error("History unavailable"); }
      const body = await response.json();
      if (serial !== searchRequest) { return; }
      if (!append) { searchResults.replaceChildren(); }
      body.entries.forEach(function (entry) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = entry.url;
        link.title = entry.kind === "search" ? "Search · " + entry.mode :
          "Started in " + entry.origin + (entry.origin_context ? " · " + entry.origin_context : "");
        const label = document.createElement("span");
        label.textContent = entry.title;
        link.appendChild(label);
        item.appendChild(link);
        searchResults.appendChild(item);
      });
      historyOffset = offset + body.entries.length;
      if (searchNone) {
        searchNone.textContent = "No history matches that search.";
        searchNone.hidden = historyOffset !== 0;
      }
      if (searchMore) { searchMore.hidden = body.entries.length < 30; }
    } catch (e) {
      if (searchNone) { searchNone.hidden = false; searchNone.textContent = "Could not load history."; }
    }
  }

  function openChatSearch() {
    if (!searchScrim) { return; }
    searchScrim.hidden = false;
    if (searchInput) { searchInput.focus(); searchInput.select(); }
    loadHistory();
  }
  function closeChatSearch() {
    if (searchScrim) { searchScrim.hidden = true; }
    if (searchOpen) { searchOpen.focus(); }
  }

  if (searchOpen) { searchOpen.addEventListener("click", openChatSearch); }
  if (searchClose) { searchClose.addEventListener("click", closeChatSearch); }
  if (searchScrim) {
    // Click the backdrop, not the panel.
    searchScrim.addEventListener("click", function (e) {
      if (e.target === searchScrim) { closeChatSearch(); }
    });
  }

  if (searchInput) {
    let timer;
    searchInput.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { loadHistory(); }, 180);
    });
  }
  searchKinds.forEach(function (button) {
    button.addEventListener("click", function () {
      historyKind = button.dataset.searchKind;
      searchKinds.forEach(function (other) {
        const active = other === button;
        other.classList.toggle("is-active", active);
        other.setAttribute("aria-pressed", active ? "true" : "false");
      });
      loadHistory();
    });
  });
  if (searchMore) { searchMore.addEventListener("click", function () { loadHistory(true); }); }

  // ---------- Fetch helpers ----------
  function csrfToken() {
    const tag = document.querySelector('meta[name="csrf-token"]');
    return tag ? tag.getAttribute("content") : "";
  }

  async function request(url, options) {
    const opts = Object.assign({}, options);
    // The session cookie travels automatically, so the token is what proves the
    // request came from one of our pages rather than someone else's.
    opts.headers = Object.assign({
      "X-Requested-With": "XMLHttpRequest",
      "Accept": "application/json",
      "X-CSRFToken": csrfToken(),
    }, opts.headers || {});
    const res = await fetch(url, opts);
    let body = null;
    try { body = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      // `message` as well as detail/error: an endpoint that answers with a full
      // envelope puts its explanation there, and without this the caller shows
      // "Request failed (503)" while the reason sits unread in the body.
      const msg = (body && (body.detail || body.error || body.message))
                  || ("Request failed (" + res.status + ")");
      const err = new Error(msg);
      // The parsed body travels with the error. A non-2xx response can still
      // carry something worth rendering, and it is already gone by the time a
      // caller could re-read it.
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  function postJSON(url, data) {
    return request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
    });
  }

  // ---------- Toast (reuses the flash tray) ----------
  function toast(message, category) {
    const tray = document.getElementById("flash-tray");
    if (!tray) { return; }
    const el = document.createElement("div");
    el.className = "flash flash-" + (category || "success");
    el.innerHTML = "<span></span><button type='button' class='flash-close' aria-label='Dismiss'>&times;</button>";
    el.querySelector("span").textContent = message;
    tray.appendChild(el);
    setTimeout(function () { el.remove(); }, 6000);
  }

  // ---------- Goal → matching papers panel ----------
  document.addEventListener("click", async function (e) {
    const btn = e.target.closest(".js-toggle-matches");
    if (!btn) { return; }
    const item = btn.closest(".goal-item");
    const panel = item.querySelector(".goal-matches");
    if (!panel.hidden) { panel.hidden = true; return; }

    panel.hidden = false;
    panel.innerHTML = "<p class='hint'>Loading…</p>";
    try {
      const data = await request(btn.dataset.url);
      if (!data.matched_papers.length) {
        panel.innerHTML = "<p class='empty'>No papers in the catalog match this goal yet.</p>";
        return;
      }
      const rows = data.matched_papers.map(function (p) {
        const pct = p.similarity != null ? " <span class='sim-badge'>" + Math.round(p.similarity * 100) + "%</span>" : "";
        const year = p.publication_year ? " (" + p.publication_year + ")" : "";
        return "<li><a href='/paper/" + p.paper_id + "'>" + escapeHtml(p.title) + "</a>" + year + pct + "</li>";
      }).join("");
      panel.innerHTML = "<ul>" + rows + "</ul>";
    } catch (err) {
      panel.innerHTML = "<p class='flash-error'>" + escapeHtml(err.message) + "</p>";
    }
  });

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  window.RC = { request: request, postJSON: postJSON, toast: toast, escapeHtml: escapeHtml };
})();
