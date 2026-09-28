/* notes-panel.js — the pen in the header, the docked panel, and the notepad.

   Docked rather than floating: the panel takes its own column and the page
   gives up that width, so the paper you are writing about stays readable and
   keeps working underneath you. There is no scrim, because a scrim says the
   page behind it is unusable until you deal with this — the opposite of what
   a notes panel is for.

   Two views, one at a time. Recent notes first; the + swaps the panel for a
   notepad. The notepad REPLACES the list so the writing surface gets the whole
   panel, and nothing is written until Save is pressed.

   It reads the same /notes route the full page does, negotiated by Accept, so
   the panel and the page can never disagree about what you have written. */

(function () {
  "use strict";

  var panel = document.getElementById("notes-panel");
  if (!panel) { return; }                       // signed out: no panel rendered

  var toggle = document.getElementById("notes-panel-toggle");
  var listView = document.getElementById("notes-view-list");
  var padView = document.getElementById("notes-view-pad");
  var list = document.getElementById("notes-panel-list");
  var heading = document.getElementById("notes-panel-title");

  var newBtn = document.getElementById("notes-new");
  var backBtn = document.getElementById("notes-back");
  var searchBtn = document.getElementById("notes-search-toggle");
  var searchRow = document.getElementById("notes-search-row");
  var searchBox = document.getElementById("notes-search");
  var openAll = document.getElementById("notes-open-all");

  var padTitle = document.getElementById("notes-pad-title");
  var padBody = document.getElementById("notes-pad-body");
  var padPaper = document.getElementById("notes-pad-paper");
  var padId = document.getElementById("notes-pad-id");
  var attachRow = document.getElementById("notes-pad-attach");
  var attachName = document.getElementById("notes-pad-attach-name");

  var loaded = false;
  var STORE = "rc-notes-open";

  // The header's heading doubles as the note's title while the notepad is open.
  var title = window.RCNotes.titleField(heading, padTitle);

  // What the notepad held when it opened. Compared rather than counting
  // keystrokes: typing a word and deleting it again leaves nothing to lose,
  // and a prompt about it would be a prompt nobody can act on sensibly.
  var opened = { title: "", body: "" };

  function dirty() {
    return !padView.hidden
        && (padTitle.value !== opened.title || padBody.value !== opened.body);
  }

  // Returns false when the person chose to stay. Used by every exit from the
  // notepad — the back arrow, Discard, closing the panel, Escape, and leaving
  // the page — so there is one answer to "may this writing be dropped?"
  // rather than one per route out.
  function mayLeave() {
    if (!dirty()) { return true; }
    return window.confirm(
      "This note has not been saved. Leave it and lose the changes?");
  }

  function csrf() {
    var tag = document.querySelector('meta[name="csrf-token"]');
    return tag ? tag.getAttribute("content") : "";
  }

  /* ---------------------------------------------------------- open / close */

  function open() {
    panel.hidden = false;
    document.body.classList.add("notes-docked");
    if (toggle) { toggle.setAttribute("aria-expanded", "true"); }
    remember(true);

    showList();
    if (!loaded) { refresh(); }
  }

  function close() {
    if (!mayLeave()) { return; }

    // Focus first, while the panel is still in the document. Hiding the element
    // focus is inside drops it to the top of the page, which for a keyboard
    // user means losing their place entirely. Docking never STEALS focus on
    // open, so this is the only direction that needs handling.
    if (panel.contains(document.activeElement) && toggle) { toggle.focus(); }

    panel.hidden = true;
    document.body.classList.remove("notes-docked");
    if (toggle) { toggle.setAttribute("aria-expanded", "false"); }
    remember(false);
  }

  function isOpen() { return !panel.hidden; }

  // Remembered across pages: docked is a working arrangement, not a dialog, and
  // having it close itself on every navigation would make it useless for the
  // one thing it is for — keeping notes to hand while you move around.
  function remember(open) {
    try { window.localStorage.setItem(STORE, open ? "1" : "0"); } catch (e) { /* private mode */ }
  }
  function remembered() {
    try { return window.localStorage.getItem(STORE) === "1"; } catch (e) { return false; }
  }

  if (toggle) {
    toggle.addEventListener("click", function () { isOpen() ? close() : open(); });
  }
  var closeBtn = document.getElementById("notes-panel-close");
  if (closeBtn) { closeBtn.addEventListener("click", close); }

  document.addEventListener("keydown", function (e) {
    // Only from the list: Escape inside the notepad would throw away writing.
    if (e.key === "Escape" && isOpen() && padView.hidden) { close(); }
  });

  /* ------------------------------------------------------------- the views */

  function showList() {
    padView.hidden = true;
    listView.hidden = false;
    if (newBtn) { newBtn.hidden = false; }
    if (backBtn) { backBtn.hidden = true; }
    // Search belongs to the list and Back belongs to the notepad; they share
    // the slot because neither is ever useful in the other's view.
    if (searchBtn) { searchBtn.hidden = false; }
    if (openAll) { openAll.hidden = false; }
    title.asLabel(searchBox.value.trim() ? "Search results" : "Recent notes");
  }

  function showPad(note) {
    listView.hidden = true;
    padView.hidden = false;
    if (newBtn) { newBtn.hidden = true; }
    if (backBtn) { backBtn.hidden = false; }
    if (searchBtn) { searchBtn.hidden = true; }
    if (openAll) { openAll.hidden = true; }

    // "Edit note" spent the header's only line on a label. The heading now
     // carries the note's own name, which is the thing worth reading there.
    title.asTitle(note && note.title ? note.title : "");
    padId.value = note ? note.note_id : "";
    padBody.value = note ? note.note_text : "";

    // An existing note keeps whatever it was attached to; a new one attaches to
    // the page you are on.
    if (note) {
      setAttachment(note.paper_id || "", note.paper_title || "this paper");
    } else {
      attachToCurrentPaper();
    }

    opened = { title: padTitle.value, body: padBody.value };

    padBody.dispatchEvent(new Event("input", { bubbles: true }));  // sync the count
    padBody.focus();
  }

  if (newBtn) { newBtn.addEventListener("click", function () { showPad(null); }); }
  if (backBtn) {
    backBtn.addEventListener("click", function () { if (mayLeave()) { showList(); } });
  }

  var discard = document.getElementById("notes-pad-discard");
  if (discard) {
    discard.addEventListener("click", function () {
      // Discard is an explicit request to lose it, so the question is narrower
      // than mayLeave's — it asks whether they meant it, not whether they knew.
      if (dirty() && !window.confirm("Discard this note?")) { return; }
      showList();
    });
  }

  // Leaving the page entirely. The browser shows its own wording here; all it
  // takes from us is that there IS something unsaved.
  window.addEventListener("beforeunload", function (e) {
    if (!dirty()) { return; }
    e.preventDefault();
    e.returnValue = "";
  });

  /* ------------------------------------------------- what it attaches to */

  function setAttachment(id, name) {
    padPaper.value = id || "";
    if (!attachRow) { return; }
    attachRow.hidden = !id;
    if (id && attachName) { attachName.textContent = name; }
  }

  // Read from the page rather than passed in, so any page showing a paper gets
  // this without the panel needing to know which pages those are.
  function attachToCurrentPaper() {
    var host = document.querySelector("[data-paper-id]");
    var title = document.querySelector("[data-paper-title]");
    if (!host) { return setAttachment("", ""); }
    setAttachment(host.getAttribute("data-paper-id"),
                  title ? title.getAttribute("data-paper-title") : "this paper");
  }

  var detach = document.getElementById("notes-pad-detach");
  if (detach) {
    detach.addEventListener("click", function () {
      // "Almost always about the paper you are reading" is not always.
      setAttachment("", "");
      padBody.focus();
    });
  }

  /* ------------------------------------------------------------- the list */

  // Inline rather than fetched: two 24px glyphs are smaller than the request
  // that would go and get them, and they must be ready the moment the list is.
  var ICON = {
    pencil: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
          + ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
          + '<path d="M12 20h9"></path>'
          + '<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>',
    trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
         + ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
         + '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"></path></svg>',
  };

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function render(groups) {
    if (!groups.length) {
      // "Nothing written yet" is false when a search simply matched nothing,
      // and it is the sentence most likely to make someone think their notes
      // are gone.
      list.innerHTML = '<p class="empty-state">'
        + (searchBox && searchBox.value.trim()
            ? "No notes match that."
            : "Nothing written yet.")
        + "</p>";
      return;
    }

    var html = "";
    groups.forEach(function (group) {
      html += '<section class="notes-panel-group"><h3>'
           + (group.paper_id ? escapeHtml(group.title) : "Not about a paper")
           + "</h3>";
      group.notes.forEach(function (note) {
        html += '<article class="notes-panel-note" data-note-id="'
             + escapeHtml(note.note_id) + '">';
        if (note.title) {
          html += '<p class="notes-panel-note-title">' + escapeHtml(note.title) + "</p>";
        }
        html += "<p>" + escapeHtml(note.note_text) + "</p>";
        // Icons, with the word kept as the accessible name: a pen and a bin
        // read faster than two words, and in a 392px column two text buttons
        // per note compete with the note itself.
        html += '<p class="note-meta">' + note.words
             + (note.words === 1 ? " word" : " words")
             + (note.edited ? " · edited" : "")
             + ' <button type="button" class="icon-btn" data-panel-edit'
             + ' aria-label="Edit note" title="Edit">' + ICON.pencil + "</button>"
             + ' <button type="button" class="icon-btn icon-btn-danger" data-panel-delete'
             + ' aria-label="Delete note" title="Delete">' + ICON.trash + "</button>"
             + "</p></article>";
      });
      html += "</section>";
    });
    list.innerHTML = html;
  }

  var groupsCache = [];

  /* -------------------------------------------------------------- search */

  if (searchBtn) {
    searchBtn.addEventListener("click", function () {
      var opening = searchRow.hidden;
      searchRow.hidden = !opening;
      searchBtn.setAttribute("aria-expanded", String(opening));

      if (opening) { return searchBox.focus(); }
      // Closing the box clears the filter. Leaving a hidden query in place
      // would show a filtered list with nothing on screen explaining why.
      if (searchBox.value) { searchBox.value = ""; refresh(); }
      showList();
    });
  }

  if (searchBox) {
    var pending = null;
    searchBox.addEventListener("input", function () {
      // Debounced, because the query goes to the database. Every keystroke
      // would be a round trip, and the answers could arrive out of order.
      window.clearTimeout(pending);
      pending = window.setTimeout(function () { refresh().then(showList); }, 220);
    });
    searchBox.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") { return; }
      e.preventDefault();
      // Stopped here, or it bubbles to the document handler that closes the
      // whole panel — so clearing a search would shut the thing you were
      // searching. Escape backs out one step at a time.
      e.stopPropagation();
      searchBox.value = "";
      refresh().then(showList);
    });
  }

  function refresh() {
    var query = searchBox && searchBox.value.trim();
    var url = panel.getAttribute("data-notes-url")
            + (query ? "?q=" + encodeURIComponent(query) : "");

    return fetch(url, {
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        loaded = true;
        groupsCache = data.papers || [];
        render(groupsCache);
      })
      .catch(function () {
        list.innerHTML = '<p class="empty-state">Could not load your notes.</p>';
      });
  }

  function findNote(id) {
    for (var g = 0; g < groupsCache.length; g++) {
      var group = groupsCache[g];
      for (var n = 0; n < group.notes.length; n++) {
        if (group.notes[n].note_id === id) {
          return Object.assign({}, group.notes[n], {
            paper_id: group.paper_id,
            paper_title: group.title,
          });
        }
      }
    }
    return null;
  }

  /* ------------------------------------------------------- saving the pad */

  padView.addEventListener("submit", function (e) {
    e.preventDefault();
    var body = (padBody.value || "").trim();
    if (!body) { return; }

    var editing = padId.value;
    var payload = { note_text: body, title: padTitle.value.trim() };
    if (!editing) { payload.paper_id = padPaper.value || ""; }

    send(editing ? "/notes/" + editing : padView.getAttribute("action"), payload)
      .then(function (ok) {
        if (!ok) { return; }
        padTitle.value = "";
        padBody.value = "";
        opened = { title: "", body: "" };     // saved: nothing left to lose
        showList();
      });
  });

  /* --------------------------------------------------- editing and removing */

  list.addEventListener("click", function (e) {
    var article = e.target.closest(".notes-panel-note");
    if (!article) { return; }
    var id = article.getAttribute("data-note-id");

    if (e.target.closest("[data-panel-delete]")) {
      if (!window.confirm("Delete this note? This cannot be undone.")) { return; }
      return send("/notes/" + id + "/delete", {});
    }

    if (e.target.closest("[data-panel-edit]")) {
      // Opens in the notepad, the same surface it was written in — rather than
      // a prompt, which cannot show a title, a word count or what it is
      // attached to.
      var note = findNote(id);
      if (note) { showPad(note); }
    }
  });

  function send(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrf(),
      },
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) { throw new Error("failed"); }
        return refresh().then(function () { return true; });
      })
      .catch(function () {
        if (window.RC) { window.RC.toast("That did not work.", "danger"); }
        return false;
      });
  }

  /* --------------------------------------------------------------- restore */

  if (remembered()) { open(); }
})();
