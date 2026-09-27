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
