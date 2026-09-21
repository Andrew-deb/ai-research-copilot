/* chat.js — behaviour for the agent composer.

   Loaded by both the landing page and /chat, because they are the same
   interface: the landing page does not advertise the assistant, it is the
   assistant. Whatever is true of the composer has to be true in both places,
   and two copies of this is how one of them ends up subtly different.

   The form posts to /chat/ask and renders whatever envelope comes back. There
   is no agent behind that endpoint until Phase 3.4, so today every answer is
   the honest "not connected" one — but the request, the capability check, the
   error handling and the rendering are all real, which means 3.4 changes the
   server and not this file. */

(function () {
  "use strict";

  var form = document.getElementById("chat-composer");
  var input = document.getElementById("chat-input");
  if (!form || !input) { return; }

  var page = document.querySelector(".chat-page") || document.querySelector(".landing-main");
  // Everything that travels WITH the composer — on /chat the composer, chips and
  // allowance note share a .chat-stage; on the landing page the form is a direct
  // child. The thread goes before whichever it is, as a sibling, so it can take
  // the height and leave the composer pinned beneath it. Inserting it next to the
  // form instead put it *inside* the stage, where nothing gave it any height and
  // every reply simply pushed the page apart.
  var stage = form.closest(".chat-stage") || form;
  var thread = document.getElementById("chat-thread");
  var pending = false;

  // A conversation may also arrive server-rendered once persistence lands.
  if (thread && page) { page.classList.add("has-conversation"); }

  /* ---------------------------------------------------------------- render */

  function ensureThread() {
    if (thread) { return thread; }
    thread = document.createElement("div");
    thread.className = "chat-thread";
    thread.id = "chat-thread";
    thread.setAttribute("aria-live", "polite");
    stage.parentNode.insertBefore(thread, stage);
    if (page) {
      // The introduction has done its job the moment a question is asked, and
      // the layout switches from "centred stack" to "thread above a pinned
      // composer". Both are CSS; this just says which state the page is in.
      page.classList.remove("is-empty");
      page.classList.add("has-conversation");
    }
    return thread;
  }

  function scrollToLatest() {
    if (thread) { thread.scrollTop = thread.scrollHeight; }
  }

  function addMessage(role, text) {
    var el = document.createElement("article");
    el.className = "chat-msg chat-msg-" + role;
    el.textContent = text;
    ensureThread().appendChild(el);
    scrollToLatest();
    return el;
  }

  function addCitations(citations) {
    if (!citations || !citations.length) { return; }
    var ol = document.createElement("ol");
    ol.className = "chat-citations";
    citations.forEach(function (c) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "/paper/" + c.paper_id;
      a.textContent = c.title;
      li.appendChild(a);
      if (c.publication_year) {
        var year = document.createElement("span");
        year.className = "chat-citation-meta";
        year.textContent = " (" + c.publication_year + ")";
        li.appendChild(year);
      }
      ol.appendChild(li);
    });
    ensureThread().appendChild(ol);
    scrollToLatest();
  }

  function render(result) {
    if (result.answer) {
      addMessage("assistant", result.answer);
      addCitations(result.citations);
    } else if (result.message) {
      // No answer and a reason: say the reason in the thread rather than only
      // in a toast, which disappears after six seconds and takes the
      // explanation with it.
      addMessage("system", result.message);
    }
  }

  /* ----------------------------------------------------------------- send */

  function setPending(on) {
    pending = on;
    input.disabled = on;
    var send = form.querySelector(".composer-send");
    if (send) { send.disabled = on; }
  }

  async function send(question) {
    setPending(true);
    addMessage("user", question);
    input.value = "";
    autosize();

    try {
      var result = await window.RC.postJSON("/chat/ask", { question: question });
      render(result);
    } catch (err) {
      // RC.request throws on any non-2xx, which includes the 503 returned while
      // the agent is not connected and the 403/429 from the capability and
      // quota gates. All three are things the person needs told, so none are
      // swallowed — and a refusal that still carries a full envelope is
      // rendered as one, so a partial answer or its citations are not thrown
      // away along with the status code.
      if (err.body && err.body.status) {
        render(err.body);
      } else {
        addMessage("system", err.message);
      }
    } finally {
      setPending(false);
      input.focus();
    }
  }

  /* ------------------------------------------------------------- composer */

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
    var question = input.value.trim();
    if (!question || pending) { return; }
    send(question);
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
