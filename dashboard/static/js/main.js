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
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeSidebar(); } });

  // ---------- Fetch helpers ----------
  async function request(url, options) {
    const res = await fetch(url, Object.assign({
      headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
    }, options));
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
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
      },
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
