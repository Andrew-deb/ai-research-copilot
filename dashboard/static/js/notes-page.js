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
  var padId = document.getElementById("page-pad-id");
  var attachLabel = document.getElementById("page-pad-attach-label");
  var detachBtn = document.getElementById("page-pad-detach");

  // Collapse the notepad that the markup deliberately leaves open. Doing it
  // here rather than with a `hidden` attribute is what keeps the page working
  // without this file.
  padView.hidden = true;

  var tallyText = tally.textContent;

  function showList() {
    padView.hidden = true;
    listView.hidden = false;
    newBtn.hidden = false;
    backBtn.hidden = true;
    heading.textContent = "Your notes";
    tally.textContent = tallyText;
  }

  function showPad(note) {
    listView.hidden = true;
    padView.hidden = false;
    newBtn.hidden = true;
    backBtn.hidden = false;

    heading.textContent = note ? "Edit note" : "New note";
    tally.textContent = "Nothing is saved until you say so";

    padId.value = note ? note.id : "";
    padTitle.value = note ? note.title : "";
    padBody.value = note ? note.body : "";
    setAttachment(note ? note.paperId : "", note ? note.paperTitle : "");

    // A new note starts in the title; an edit starts in the body, where the
    // change almost always is.
    (note ? padBody : padTitle).focus();
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
  backBtn.addEventListener("click", showList);
  detachBtn.addEventListener("click", function () {
    setAttachment("", "");
    padBody.focus();
  });

  document.getElementById("page-pad-discard").addEventListener("click", function () {
    // Only worth asking about if there is something to lose.
    var written = padBody.value.trim() || padTitle.value.trim();
    if (written && !window.confirm("Discard this note?")) { return; }
    showList();
  });

  /* ------------------------------------------------------------- editing */

  listView.addEventListener("click", function (e) {
    if (!e.target.closest("[data-page-edit]")) { return; }
    var item = e.target.closest(".note-item");
    if (!item) { return; }

    showPad({
      id: item.getAttribute("data-note-id"),
      title: item.getAttribute("data-note-title") || "",
      body: item.querySelector(".note-text").textContent,
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
    if (!padId.value) { return; }               // a new note: leave it alone
    e.preventDefault();
    padView.setAttribute("action", "/notes/" + padId.value);
    padView.submit();
  });
})();
