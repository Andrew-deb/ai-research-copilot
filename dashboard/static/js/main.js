/* main.js — shared helpers: flash dismissal, JSON POST, toasts, goal-match panels. */

(function () {
  "use strict";

  // ---------- Flash messages ----------
  document.addEventListener("click", function (e) {
    if (e.target.classList.contains("flash-close")) {
      e.target.closest(".flash").remove();
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

  // ---------- Sidebar (mobile) ----------
  const sidebar = document.getElementById("sidebar");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const scrim = document.getElementById("sidebar-scrim");
  function closeSidebar() {
    if (sidebar) { sidebar.classList.remove("open"); }
    if (scrim) { scrim.hidden = true; }
  }
  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener("click", function () {
      const open = sidebar.classList.toggle("open");
      if (scrim) { scrim.hidden = !open; }
    });
  }
  if (scrim) { scrim.addEventListener("click", closeSidebar); }
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") { return; }
    // Innermost layer first: a dialog over the sidebar should close alone.
    const dialog = document.getElementById("chat-search-scrim");
    if (dialog && !dialog.hidden) { dialog.hidden = true; return; }
    closeSidebar();
  });

  // Ctrl/Cmd+K is what every product with this dialog uses.
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const opener = document.getElementById("chat-search-open");
      if (opener) { opener.click(); }
    }
  });

  // ---------- Sidebar collapse (desktop) ----------
  // Separate from the mobile toggle above: that one slides an off-canvas panel
  // in and out, this one hides a panel that is always there. The preference is
  // remembered, because re-collapsing it on every page load would make the
  // control feel broken.
  const shell = document.querySelector(".app-shell");
  const collapseBtn = document.getElementById("sidebar-collapse");
  const COLLAPSE_KEY = "rc-sidebar-collapsed";

  function setCollapsed(collapsed) {
    if (!shell) { return; }
    shell.classList.toggle("sidebar-collapsed", collapsed);
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (e) {}
  }

  try {
    if (localStorage.getItem(COLLAPSE_KEY) === "1") { setCollapsed(true); }
  } catch (e) {}

  if (collapseBtn) {
    collapseBtn.addEventListener("click", function () {
      setCollapsed(!shell.classList.contains("sidebar-collapsed"));
    });
  }

  // The topbar control brings a collapsed sidebar back, so a visitor who hides it
  // is never stranded without navigation.
  if (sidebarToggle && shell) {
    sidebarToggle.addEventListener("click", function () {
      if (shell.classList.contains("sidebar-collapsed")) { setCollapsed(false); }
    });
  }

  // ---------- Chat history search dialog ----------
  // Filters conversation titles by keyword. Deliberately NOT the corpus search:
  // that one runs semantic retrieval over papers and already has its own page.
  const searchScrim = document.getElementById("chat-search-scrim");
  const searchOpen = document.getElementById("chat-search-open");
  const searchClose = document.getElementById("chat-search-close");
  const searchInput = document.getElementById("chat-search-input");
  const searchResults = document.getElementById("chat-search-results");
  const searchNone = document.getElementById("chat-search-none");

  function openChatSearch() {
    if (!searchScrim) { return; }
    searchScrim.hidden = false;
    if (searchInput) { searchInput.focus(); searchInput.select(); }
  }
  function closeChatSearch() {
    if (searchScrim) { searchScrim.hidden = true; }
  }

  if (searchOpen) { searchOpen.addEventListener("click", openChatSearch); }
  if (searchClose) { searchClose.addEventListener("click", closeChatSearch); }
  if (searchScrim) {
    // Click the backdrop, not the panel.
    searchScrim.addEventListener("click", function (e) {
      if (e.target === searchScrim) { closeChatSearch(); }
    });
  }

  if (searchInput && searchResults) {
    searchInput.addEventListener("input", function () {
      const q = searchInput.value.trim().toLowerCase();
      let shown = 0;
      searchResults.querySelectorAll("li").forEach(function (li) {
        const link = li.querySelector("a");
        const title = link ? (link.dataset.title || "") : "";
        const match = !q || title.indexOf(q) !== -1;
        li.hidden = !match;
        if (match) { shown += 1; }
      });
      if (searchNone) { searchNone.hidden = shown !== 0; }
    });
  }

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
      const msg = (body && (body.detail || body.error)) || ("Request failed (" + res.status + ")");
      throw new Error(msg);
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
