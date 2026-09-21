/* chat.js — behaviour for the agent composer.

   Loaded by both the landing page and /chat, because they are the same
   interface: the landing page does not advertise the assistant, it is the
   assistant. Whatever is true of the composer has to be true in both places,
   and two copies of this is how one of them ends up subtly different.

   Placeholder behaviour only for now. There is no agent behind the form yet, so
   submitting says so rather than pretending to think. Phase 3.3 replaces the
   submit handler with real orchestration; everything else here survives. */

(function () {
  "use strict";

  var form = document.getElementById("chat-composer");
  var input = document.getElementById("chat-input");
  if (!form || !input) { return; }

  // Chips fill the box rather than navigating. A suggestion that took you to
  // another page would teach the wrong thing about the control above it.
  Array.prototype.forEach.call(
    document.querySelectorAll(".prompt-chip[data-prompt]"),
    function (chip) {
      chip.addEventListener("click", function () {
        input.value = chip.dataset.prompt;
        input.focus();
        input.dispatchEvent(new Event("input"));
      });
    }
  );

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  }

  input.addEventListener("input", autosize);

  // Enter sends, Shift+Enter breaks the line — the convention everywhere else,
  // and getting it backwards is the fastest way to lose a half-typed question.
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!input.value.trim()) { return; }
    window.RC.toast("The research assistant connects in the next release. " +
                    "Semantic search answers with citations today.", "info");
  });

  // A question may arrive already in the box via ?q=. Size it to what it holds
  // and put the caret at the end, so it reads as something to edit rather than
  // placeholder text sitting in the way.
  if (input.value.trim()) {
    autosize();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
})();
