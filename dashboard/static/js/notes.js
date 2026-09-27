/* notes.js — editing a note in place, and counting what you have written.

   Progressive enhancement throughout. Every edit form on the page is a real
   form with a real action, so without this file the page still works — you get
   all the edit boxes open at once rather than one at a time, which is untidy
   rather than broken. Nothing here is required for a note to be saved.

   Deletion is not handled here: main.js already intercepts [data-confirm] for
   the whole site, and a second confirmation path would be one more place for
   the two to disagree about what "are you sure" means. */

(function () {
  "use strict";

  var items = document.querySelectorAll(".note-item");
  if (!items.length && !document.querySelector("[data-word-count]")) { return; }

  // Collapse the editors that the markup deliberately left open. Doing it here
  // rather than with a `hidden` attribute is what makes the page work without
  // this file: no JS means every note is editable in place instead of none.
  items.forEach(function (item) {
    var form = item.querySelector(".note-edit");
    if (form) { form.hidden = true; }
  });

  /* --------------------------------------------------------------- editing */

  function editor(item) { return item.querySelector(".note-edit"); }
  function reader(item) { return item.querySelector(".note-read"); }

  function closeEditor(item) {
    var form = editor(item);
    if (!form) { return; }
    form.hidden = true;
    reader(item).hidden = false;
    // Put the original text back, so cancelling twice does not leave the second
    // attempt showing the abandoned first one.
    var field = form.querySelector("textarea");
    if (field) { field.value = field.defaultValue; count(field); }
  }

  function openEditor(item) {
    // One at a time. Several open editors is several sets of unsaved changes,
    // and no way to tell which of them you are about to lose.
    document.querySelectorAll(".note-item").forEach(function (other) {
      if (other !== item) { closeEditor(other); }
    });

    var form = editor(item);
    if (!form) { return; }
    reader(item).hidden = true;
    form.hidden = false;

    var field = form.querySelector("textarea");
    if (field) {
      field.focus();
      // Caret at the end rather than selecting everything: an edit is usually
      // an addition, and selecting all means the first keystroke destroys it.
      field.setSelectionRange(field.value.length, field.value.length);
      count(field);
    }
  }

  document.addEventListener("click", function (e) {
    var edit = e.target.closest("[data-edit-note]");
    if (edit) { return openEditor(edit.closest(".note-item")); }

    var cancel = e.target.closest("[data-cancel-edit]");
    if (cancel) { return closeEditor(cancel.closest(".note-item")); }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") { return; }
    var open = e.target.closest(".note-edit");
    if (open) { closeEditor(open.closest(".note-item")); }
  });

  /* ------------------------------------------------------------ word count */

  var LIMIT = 10000;   // characters, matching NOTE_MAX_CHARS on the server

  function words(text) {
    // Split on any whitespace, and drop the empty strings an empty or
    // trailing-space field produces. Matches progress_service.word_count, so
    // the number does not change when the page reloads.
    var parts = (text || "").split(/\s+/).filter(Boolean);
    return parts.length;
  }

  function count(field) {
    var label = document.querySelector('[data-word-count-for="' + field.id + '"]');
    if (!label) { return; }

    var n = words(field.value);
    label.textContent = n + (n === 1 ? " word" : " words");

    // The character limit is what the server enforces, so that is what gets
    // flagged — a note can be short in words and still too long to save.
    var over = field.value.length > LIMIT;
    label.classList.toggle("is-over", over);
    if (over) {
      label.textContent += " · too long to save";
    }
  }

  document.addEventListener("input", function (e) {
    if (e.target.matches("[data-word-count]")) { count(e.target); }
  });

  document.querySelectorAll("[data-word-count]").forEach(count);
})();
