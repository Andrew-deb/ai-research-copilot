/* palette.js — one box that finds anything, from anywhere.

   Ctrl+K or Cmd+K on every page. A dialog rather than a docked panel, unlike
   the notes one: this exists to take you somewhere else, so the page behind it
   is on its way out rather than something you keep working in.

   The timings that shaped this, measured against the deployed Lakebase:

       one round trip, doing nothing          438 ms
       the old per-section paper lookup      3574 ms

   So: one request per search rather than four, a 250 ms debounce rather than
   120, a two-character minimum, and the in-flight request is ABORTED when a
   newer keystroke replaces it. An ignored response still holds a server thread
   and a pooled connection until it finishes — which is how the first version
   exhausted the pool and then answered every search with silence. */

(function () {
  "use strict";

  var palette = document.getElementById("palette");
  if (!palette) { return; }

  var input = document.getElementById("palette-input");
  var results = document.getElementById("palette-results");
  var scrim = document.getElementById("palette-scrim");
  var trigger = document.getElementById("palette-open");

  var items = [];        // flattened, in the order they appear
  var cursor = -1;
  var lastFocused = null;
  var pending = null;
  var sequence = 0;      // so a slow answer cannot overwrite a newer one
  var inflight = null;

  /* ---------------------------------------------------------------- icons */

  function svg(path) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
         + ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
         + path + "</svg>";
  }

  var ICONS = {
    dashboard: svg('<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.8-3.8"/>'),
    folder: svg('<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'),
    progress: svg('<rect width="7" height="18" x="3" y="3" rx="1"/><rect width="7" height="11" x="14" y="3" rx="1"/>'),
    note: svg('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>'),
    target: svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>'),
    book: svg('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/>'),
    info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>'),
    spark: svg('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"/>'),
    more: svg('<path d="M5 12h.01M12 12h.01M19 12h.01"/>'),
    dot: svg('<circle cx="12" cy="12" r="3"/>')
  };

  // The word saying what a row IS, so a note and a paper with similar names are
  // not two identical-looking lines.
  var KIND = {
    paper: "Paper",
    note: "Note",
    collection: "Collection",
    conversation: "Chat"
  };

  /* ------------------------------------------------------------ open/close */

  function open() {
    if (!palette.hidden) { return; }
    lastFocused = document.activeElement;
    palette.hidden = false;
    document.body.classList.add("palette-open");
    input.value = "";
    input.focus();
    query("");                        // destinations and recent work
  }

  function close() {
    if (palette.hidden) { return; }
    if (inflight) { inflight.abort(); inflight = null; }
    palette.hidden = true;
    document.body.classList.remove("palette-open");
    results.innerHTML = "";
    items = [];
    cursor = -1;
    // Back where they were. A dialog that drops focus at the top of the
    // document makes the shortcut cost more than it saves.
    if (lastFocused && lastFocused.focus) { lastFocused.focus(); }
  }

  if (trigger) { trigger.addEventListener("click", open); }
  if (scrim) { scrim.addEventListener("click", close); }

  document.addEventListener("keydown", function (e) {
    // Cmd on a Mac, Ctrl everywhere else. main.js had this same combination
    // bound to the chat-history search and won by being registered first, so
    // the global palette was unreachable by its own shortcut; that one is now
    // Ctrl+Shift+F.
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      return palette.hidden ? open() : close();
    }
    if (e.key === "Escape" && !palette.hidden) {
      e.preventDefault();
      close();
    }
  });

  /* ------------------------------------------------------------- rendering */

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  // Pages are tiles and assets are rows, because they are different kinds of
  // answer: a destination is one of nine fixed things, recognised by its shape;
  // an asset is one of thousands and has to be read.
  // Five fit the row beside the More toggle. The rest are rendered but folded
  // away — they are still in the DOM, so arrowing through reaches them and
  // expanding costs no request.
  var TILES_SHOWN = 5;

  function tile(item, index, extra) {
    return '<a class="palette-tile' + (extra ? " is-extra" : "")
         + '" role="option" aria-selected="false"'
         + ' data-index="' + index + '" href="' + escapeHtml(item.url) + '">'
         + (ICONS[item.icon] || ICONS.dot)
         + "<span>" + escapeHtml(item.label) + "</span></a>";
  }

  function moreTile(hidden) {
    return '<button type="button" class="palette-tile palette-tile-more"'
         + ' data-tile-more data-count="' + hidden + '" aria-expanded="false">'
         + ICONS.more + "<span>" + hidden + " more</span></button>";
  }

  function row(item, index) {
    return '<a class="palette-item" role="option" aria-selected="false"'
         + ' data-index="' + index + '" href="' + escapeHtml(item.url) + '">'
         + '<span class="palette-item-kind">' + escapeHtml(KIND[item.kind] || "") + "</span>"
         + '<span class="palette-item-label">' + escapeHtml(item.label) + "</span>"
         + (item.detail
             ? '<span class="palette-item-detail">' + escapeHtml(item.detail) + "</span>"
             : "")
         + "</a>";
  }

  function paint(sections, note) {
    var html = note ? '<p class="palette-empty">' + note + "</p>" : "";

    sections.forEach(function (section) {
      var tiles = section.kind === "pages";
      html += '<div class="palette-section"><h2>' + escapeHtml(section.title) + "</h2>"
           + (tiles ? '<div class="palette-tiles">' : "");

      section.items.forEach(function (item, n) {
        var index = items.length;
        items.push(item);
        html += tiles ? tile(item, index, n >= TILES_SHOWN) : row(item, index);
      });

      if (tiles && section.items.length > TILES_SHOWN) {
        html += moreTile(section.items.length - TILES_SHOWN);
      }
      html += (tiles ? "</div>" : "") + "</div>";
    });

    results.innerHTML = html;
    // First row pre-selected, or Enter straight after typing does nothing.
    move(0);
  }

  function render(data) {
    var sections = data.sections || [];
    items = [];

    if (data.ok === false) {
      // NOT "nothing matches". Saying that when the database is unreachable
      // tells somebody they have no notes, which is how the first version of
      // this failed — silently, and convincingly.
      return paint(sections, "Search is unavailable just now. "
                           + "The pages above still work.");
    }
    if (!sections.length) {
      results.innerHTML = '<p class="palette-empty">Nothing matches that.</p>';
      return;
    }
    paint(sections, null);
  }

  /* ------------------------------------------------------------- searching */

  function query(text) {
    var mine = ++sequence;

    // Cancelled, not merely ignored. An ignored response still holds a server
    // thread and a pooled connection until it finishes.
    if (inflight) { inflight.abort(); }
    inflight = new AbortController();

    return fetch(palette.getAttribute("data-command-url")
                 + "?q=" + encodeURIComponent(text), {
      signal: inflight.signal,
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (mine !== sequence) { return; }   // a newer answer already landed
        render(data);
      })
      .catch(function (err) {
        // An abort is this code's own doing, not a failure to report.
        if (err && err.name === "AbortError") { return; }
        if (mine === sequence) {
          results.innerHTML = '<p class="palette-empty">Could not search just now.</p>';
        }
      });
  }

  input.addEventListener("input", function () {
    window.clearTimeout(pending);
    // 250ms, not 120. One round trip is 438ms before the database does any
    // work, so a shorter wait only stacks requests the next keystroke makes
    // obsolete.
    pending = window.setTimeout(function () { query(input.value.trim()); }, 250);
  });

  /* ------------------------------------------------------------- selecting */

  function move(next) {
    if (!items.length) { cursor = -1; return; }
    // Wraps, because a list you can fall off the end of makes you look at the
    // screen to find out where you are.
    cursor = (next + items.length) % items.length;

    results.querySelectorAll("[data-index]").forEach(function (el, i) {
      var on = i === cursor;
      el.classList.toggle("is-active", on);
      el.setAttribute("aria-selected", String(on));
      if (on) { el.scrollIntoView({ block: "nearest" }); }
    });
  }

  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); return move(cursor + 1); }
    if (e.key === "ArrowUp") { e.preventDefault(); return move(cursor - 1); }
    if (e.key === "Enter") {
      var chosen = items[cursor];
      if (!chosen) { return; }
      e.preventDefault();
      window.location.href = chosen.url;
    }
  });

  // Hovering moves the selection, so the mouse and the keyboard never disagree
  // about which row Enter would take.
  results.addEventListener("mousemove", function (e) {
    var el = e.target.closest("[data-index]");
    if (el) { move(parseInt(el.getAttribute("data-index"), 10)); }
  });

  results.addEventListener("click", function (e) {
    var more = e.target.closest("[data-tile-more]");
    if (!more) { return; }
    e.preventDefault();

    var grid = more.closest(".palette-tiles");
    var open = grid.classList.toggle("is-expanded");
    more.setAttribute("aria-expanded", String(open));
    // Hidden tiles were always in the DOM and always arrow-reachable, so this
    // only changes what is drawn — nothing is fetched and no index shifts.
    more.querySelector("span").textContent = open ? "Fewer" : more.dataset.count + " more";
  });
})();
