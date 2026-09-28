/* notes-page.js — the Notes page, on the same two views as the panel.

   Recent notes first; the + swaps them for a notepad in the same place, so the
   page never jumps and there is only ever one thing to read. Save and Discard
   return to the list.

   This replaces the earlier in-place edit boxes. Editing a note now opens the
   notepad it was written in — the same surface, with its title, word count and
   what it is attached to — rather than a bare textarea wedged into a list row.

   Without JavaScript the page is still the list: the notepad is a real form
   posting to a real endpoint, so pressing + and saving works; you simply see
   both views at once. */

(function () {
  "use strict";

  var page = document.getElementById("notes-page");
  if (!page) { return; }

  var listView = document.getElementById("page-view-list");
  var padView = document.getElementById("page-view-pad");
  var newBtn = document.getElementById("page-note-new");
  var backBtn = document.getElementById("page-note-back");
  var heading = document.getElementById("notes-page-title");
  var tally = document.getElementById("notes-page-tally");

  var padTitle = document.getElementById("page-pad-title");
  var padBody = document.getElementById("page-pad-body");
  var padPaper = document.getElementById("page-pad-paper");
  var padTags = document.getElementById("page-pad-tags");
  var padId = document.getElementById("page-pad-id");
  var attachLabel = document.getElementById("page-pad-attach-label");
  var detachBtn = document.getElementById("page-pad-detach");

  // Collapse the notepad that the markup deliberately leaves open. Doing it
  // here rather than with a `hidden` attribute is what keeps the page working
  // without this file.
  padView.hidden = true;

  var tallyText = tally.textContent;

  // The page heading doubles as the note's title while the notepad is open.
  var title = window.RCNotes.titleField(heading, padTitle);

  // What the notepad held when it opened. Compared rather than counting
  // keystrokes: typing a word and deleting it again leaves nothing to lose.
  var opened = { title: "", body: "", tags: "" };

  function dirty() {
    return !padView.hidden
        && (padTitle.value !== opened.title
            || padBody.value !== opened.body
            || padTags.value !== opened.tags);
  }

  // One answer to "may this writing be dropped?", shared by every way out of
  // the notepad — the back arrow, Discard, and leaving the page.
  function mayLeave() {
    if (!dirty()) { return true; }
    return window.confirm(
      "This note has not been saved. Leave it and lose the changes?");
  }

  function showList() {
    padView.hidden = true;
    listView.hidden = false;
    newBtn.hidden = false;
    backBtn.hidden = true;
    title.asLabel("Your notes");
    tally.textContent = tallyText;
    // Both classes come off together. `app-fixed` caps the shell at the
    // viewport, which is what gives the notepad a height to stretch into — and
    // also what forces the LIST to scroll inside its own box, putting a second
    // scrollbar down the middle of the page. The list wants the window's
    // scrollbar and no other, so the cap only exists while writing.
    document.body.classList.remove("notes-writing", "app-fixed");
  }

  function showPad(note) {
    listView.hidden = true;
    padView.hidden = false;
    newBtn.hidden = true;
    backBtn.hidden = false;

    title.asTitle(note ? note.title : "");
    tally.textContent = "Double-click the title to rename · nothing is saved until you say so";

    padId.value = note ? note.id : "";
    padBody.value = note ? note.body : "";
    padTags.value = note ? note.tags : "";
    setAttachment(note ? note.paperId : "", note ? note.paperTitle : "");

    opened = { title: padTitle.value, body: padBody.value, tags: padTags.value };
    document.body.classList.add("notes-writing", "app-fixed");

    // Always the body: the title is optional and a rename away, so opening
    // in it would put a field nobody has to fill in front of the one they came
    // to write in.
    padBody.focus();
    padBody.dispatchEvent(new Event("input", { bubbles: true }));   // sync the count
  }

  function setAttachment(id, title) {
    padPaper.value = id || "";
    attachLabel.textContent = id
      ? "On " + (title || "this paper")
      : "Not attached to a paper";
    detachBtn.hidden = !id;
  }

  newBtn.addEventListener("click", function () { showPad(null); });
  backBtn.addEventListener("click", function () { if (mayLeave()) { showList(); } });
  detachBtn.addEventListener("click", function () {
    setAttachment("", "");
    padBody.focus();
  });

  document.getElementById("page-pad-discard").addEventListener("click", function () {
    // Discard is an explicit request to lose it, so the question is narrower
    // than mayLeave's — it asks whether they meant it, not whether they knew.
    if (dirty() && !window.confirm("Discard this note?")) { return; }
    showList();
  });

  // Leaving the page entirely — a nav click, the back button, a closed tab.
  // The browser supplies its own wording; all it needs from us is that there
  // IS something unsaved.
  window.addEventListener("beforeunload", function (e) {
    if (!dirty()) { return; }
    e.preventDefault();
    e.returnValue = "";
  });

  /* ------------------------------------------------------------- editing */

  listView.addEventListener("click", function (e) {
    if (!e.target.closest("[data-page-edit]")) { return; }
    var item = e.target.closest(".note-item");
    if (!item) { return; }

    showPad({
      id: item.getAttribute("data-note-id"),
      title: item.getAttribute("data-note-title") || "",
      // The rendered Markdown is not the note: read the source off the
      // element instead, or editing would save the HTML back over the text.
      body: item.getAttribute("data-note-body"),
      tags: item.getAttribute("data-note-tags") || "",
      paperId: item.getAttribute("data-note-paper") || "",
      paperTitle: item.getAttribute("data-note-paper-title") || "",
    });
  });

  /* -------------------------------------------------------------- saving */

  // An edit posts to the note's own URL. The form's action is the create
  // endpoint, so editing has to retarget it — done on submit rather than when
  // the notepad opens, so a discarded edit never leaves the form pointing
  // somewhere the next new note would follow.
  padView.addEventListener("submit", function (e) {
    // Saving is not leaving. Cleared before the navigation the submit causes,
    // or the browser would ask them to confirm discarding what they just saved.
    opened = { title: padTitle.value, body: padBody.value, tags: padTags.value };

    if (!padId.value) { return; }               // a new note: the action is right

    // An edit posts to the note's own URL. Retargeted here rather than when the
    // notepad opens, so a discarded edit never leaves the form pointing at a
    // note the next new one would overwrite.
    e.preventDefault();
    padView.setAttribute("action", "/notes/" + padId.value);
    padView.submit();
  });
})();
