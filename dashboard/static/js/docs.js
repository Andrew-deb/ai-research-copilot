/* docs.js — filters help and documentation articles as you type.

   The box does real work. A search input that only looks like one is the same
   broken promise as a button that goes nowhere, and it is worse here: someone
   who types a question and gets no response concludes the answer does not exist
   rather than that the control is decorative. */

(function () {
  "use strict";

  const input = document.getElementById("doc-filter");
  const body = document.getElementById("doc-body");
  const none = document.getElementById("doc-none");
  if (!input || !body) { return; }

  const items = Array.from(body.querySelectorAll(".doc-item")).map(function (el) {
    return { el: el, text: el.textContent.toLowerCase() };
  });

  function apply() {
    const query = input.value.trim().toLowerCase();
    // Every word must appear somewhere in the article, so "limit account"
    // narrows rather than widening the way a single-term match would.
    const terms = query ? query.split(/\s+/) : [];
    let shown = 0;

    items.forEach(function (item) {
      const match = terms.every(function (t) { return item.text.indexOf(t) !== -1; });
      item.el.hidden = !match;
      if (match) { shown += 1; }
    });

    if (none) { none.hidden = shown !== 0; }
  }

  input.addEventListener("input", apply);

  // Escape clears rather than closing: there is nothing to close, and an empty
  // box with every article back is the state someone actually wants.
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { input.value = ""; apply(); }
  });
})();
