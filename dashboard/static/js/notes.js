/* notes.js — counting what you have written.

   This file used to hold an in-place editor as well: each note in the list had
   a hidden form beside it, and Edit swapped one for the other. The notepad
   replaced that. Editing now opens the same surface a note was written in,
   with its title, its word count and what it is attached to — none of which a
   textarea wedged into a list row could show.

   What is left is the word count, which every writing surface shares: the
   panel's notepad, the page's notepad, and any textarea marked
   `data-word-count` that comes later. Loaded shell-wide because the panel is. */

(function () {
  "use strict";

  var LIMIT = 10000;   // characters, matching NOTE_MAX_CHARS on the server

  function words(text) {
    // Split on any whitespace, and drop the empty strings an empty or
    // trailing-space field produces. Matches progress_service.word_count, so
    // the number does not change when the page reloads.
    return (text || "").split(/\s+/).filter(Boolean).length;
  }

  function count(field) {
    var label = document.querySelector('[data-word-count-for="' + field.id + '"]');
    if (!label) { return; }

    var n = words(field.value);
    label.textContent = n + (n === 1 ? " word" : " words");

    // The character limit is what the server enforces, so that is what gets
    // flagged — a note can be short in words and still too long to save, and
    // finding that out on submit is finding it out too late.
    var over = field.value.length > LIMIT;
    label.classList.toggle("is-over", over);
    if (over) { label.textContent += " · too long to save"; }
  }

  document.addEventListener("input", function (e) {
    if (e.target.matches("[data-word-count]")) { count(e.target); }
  });

  document.querySelectorAll("[data-word-count]").forEach(count);
})();

/* --- The heading IS the title ----------------------------------------------
   A notepad headed "Edit note" wastes its largest piece of type on a label,
   and then asks for the title again in a field below it. The heading shows the
   note's own name instead, and a double-click edits it in place.

   The <input> is still the form field — it posts, it validates, it carries the
   value. It is simply not what you look at until you want to change it. */

window.RCNotes = window.RCNotes || {};

window.RCNotes.titleField = function (heading, input, opts) {
  var placeholder = (opts && opts.placeholder) || "Untitled note";
  var editable = false;

  function show() {
    heading.textContent = input.value.trim() || placeholder;
    heading.classList.toggle("is-untitled", !input.value.trim());
  }

  function startEdit() {
    if (!editable) { return; }
    heading.hidden = true;
    input.hidden = false;
    input.focus();
    input.select();
  }

  function endEdit() {
    input.hidden = true;
    heading.hidden = false;
    show();
  }

  heading.addEventListener("dblclick", startEdit);
  // A double-click is not discoverable on its own and impossible on a touch
  // screen, so Enter and Space on the focused heading do the same thing.
  heading.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); startEdit(); }
  });

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); endEdit(); heading.focus(); }
    else if (e.key === "Escape") { e.preventDefault(); endEdit(); }
  });
  input.addEventListener("blur", endEdit);

  return {
    // Called when the notepad opens or closes: a heading is only a title while
    // there is a note under it.
    asTitle: function (value) {
      editable = true;
      input.value = value || "";
      heading.setAttribute("tabindex", "0");
      heading.setAttribute("title", "Double-click to rename");
      heading.classList.add("is-note-title");
      endEdit();
    },
    asLabel: function (text) {
      editable = false;
      input.hidden = true;
      heading.hidden = false;
      heading.removeAttribute("tabindex");
      heading.removeAttribute("title");
      heading.classList.remove("is-note-title", "is-untitled");
      heading.textContent = text;
    },
  };
};

/* --- Markdown bodies -------------------------------------------------------
   A research note is written the way notes are written: bullets, a numbered
   list of things to check, the odd emphasis. Rendering it makes the structure
   the author put there visible instead of showing them their own asterisks.

   Upgraded in place rather than rendered on the server. The escaped text is
   what ships, so a page with neither library — a failed CDN, a blocked script
   — shows the note exactly as typed rather than nothing at all. */

(function () {
  "use strict";

  var targets = document.querySelectorAll("[data-markdown]");
  if (!targets.length) { return; }

  // Both or neither. Rendering Markdown without the sanitiser would turn
  // stored text into live HTML, which is the one thing this must never do —
  // and a note is not always written by the person reading it: the agent
  // writes them too, on request, from text it did not author either.
  if (typeof window.marked === "undefined" || typeof window.DOMPurify === "undefined") {
    return;
  }

  window.marked.setOptions({ breaks: true, gfm: true });

  targets.forEach(function (el) {
    try {
      var html = window.DOMPurify.sanitize(window.marked.parse(el.textContent));
      el.innerHTML = html;
      el.classList.add("is-markdown");
    } catch (e) {
      // Leave the plain text in place. A note that renders as typed is a
      // small loss; a blank note is the loss of the thing itself.
    }
  });
})();
