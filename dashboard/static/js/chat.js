/* chat.js — the agent composer and the conversation it produces.

   Loaded by both the landing page and /chat, because they are the same
   interface: the landing page does not advertise the assistant, it is the
   assistant. Whatever is true of the composer has to be true in both places,
   and two copies of this is how one of them ends up subtly different.

   The turn is streamed. /chat/ask answers Server-Sent Events when asked to, and
   the steps below are the agent's real ones — the tool it called, the query it
   used, how many papers came back. A research turn takes the better part of a
   minute, and a silent minute reads as a hang. Inventing plausible-looking
   phases would have been easier and would have been fiction. */

(function () {
  "use strict";

  var form = document.getElementById("chat-composer");
  var input = document.getElementById("chat-input");
  if (!form || !input) { return; }

  var page = document.querySelector(".chat-page") || document.querySelector(".landing-main");
  var stage = form.closest(".chat-stage") || form;
  var thread = document.getElementById("chat-thread");
  var pending = false;

  if (thread && page) { page.classList.add("has-conversation"); }

  /* ------------------------------------------------------------ rendering */

  function ensureThread() {
    if (thread) { return thread; }
    thread = document.createElement("div");
    thread.className = "chat-thread";
    thread.id = "chat-thread";
    thread.setAttribute("aria-live", "polite");
    stage.parentNode.insertBefore(thread, stage);
    if (page) {
      page.classList.remove("is-empty");
      page.classList.add("has-conversation");
    }
    return thread;
  }

  function scrollToLatest() {
    if (thread) { thread.scrollTop = thread.scrollHeight; }
  }

  // Markdown, sanitised. The answer is model-generated, so it is untrusted
  // input no matter how it reads — DOMPurify does the escaping, because
  // hand-rolling that is how cross-site scripting gets written. If either
  // library failed to load the text still shows, just without formatting.
  function renderMarkdown(el, text) {
    if (window.marked && window.DOMPurify) {
      el.innerHTML = window.DOMPurify.sanitize(
        window.marked.parse(text, { breaks: true, gfm: true })
      );
    } else {
      el.textContent = text;
    }
  }

  function addUserMessage(text) {
    var el = document.createElement("article");
    el.className = "chat-msg chat-msg-user";
    el.textContent = text;
    ensureThread().appendChild(el);
    scrollToLatest();
  }

  function addAnswer(text) {
    var el = document.createElement("article");
    el.className = "chat-msg chat-msg-assistant";
    renderMarkdown(el, text);
    ensureThread().appendChild(el);
    scrollToLatest();
    return el;
  }

  function addNotice(text) {
    var el = document.createElement("article");
    el.className = "chat-msg chat-msg-system";
    el.textContent = text;
    ensureThread().appendChild(el);
    scrollToLatest();
  }

  /* ---------------------------------------------------------------- steps */

  // One live panel per turn: the agent's steps as they happen, collapsed to a
  // one-line summary once the answer arrives. Keeping the trace rather than
  // discarding it means a reader can still see what the answer was built from.
  function createTrace() {
    var box = document.createElement("div");
    box.className = "chat-trace is-running";

    var summary = document.createElement("button");
    summary.type = "button";
    summary.className = "chat-trace-summary";
    summary.setAttribute("aria-expanded", "true");

    var steps = document.createElement("ol");
    steps.className = "chat-trace-steps";

    summary.addEventListener("click", function () {
      var open = box.classList.toggle("is-open");
      summary.setAttribute("aria-expanded", open ? "true" : "false");
    });

    box.appendChild(summary);
    box.appendChild(steps);
    ensureThread().appendChild(box);

    var started = Date.now();
    var counts = { tools: 0, papers: 0 };
    var current = null;

    function setSummary(text) { summary.textContent = text; }
    setSummary("Thinking…");

    return {
      status: function (phase) {
        var label = { thinking: "Thinking…", reading: "Reading results…",
                      writing: "Writing the answer…" }[phase];
        if (label) { setSummary(label); }
      },
      toolStart: function (name, args) {
        counts.tools += 1;
        current = document.createElement("li");
        current.className = "chat-step is-running";
        var what = (args && (args.query || args.topic || args.paper_id)) || "";
        current.textContent = prettyTool(name) + (what ? " · " + what : "");
        steps.appendChild(current);
        setSummary(prettyTool(name) + "…");
        scrollToLatest();
      },
      toolEnd: function (ok, found, error) {
        if (!current) { return; }
        current.classList.remove("is-running");
        current.classList.add(ok ? "is-done" : "is-failed");
        if (ok && found) {
          counts.papers += found;
          var n = document.createElement("span");
          n.className = "chat-step-count";
          n.textContent = found + (found === 1 ? " paper" : " papers");
          current.appendChild(n);
        } else if (!ok && error) {
          var e = document.createElement("span");
          e.className = "chat-step-count";
          e.textContent = error;
          current.appendChild(e);
        }
        current = null;
      },
      finish: function () {
        box.classList.remove("is-running");
        var seconds = Math.round((Date.now() - started) / 1000);
        var bits = [];
        if (counts.tools) {
          bits.push(counts.tools + (counts.tools === 1 ? " step" : " steps"));
        }
        if (counts.papers) { bits.push(counts.papers + " papers"); }
        bits.push(seconds + "s");
        setSummary(bits.join(" · "));
      },
    };
  }

  function prettyTool(name) {
    return {
      search_papers: "Searching papers",
      get_paper_details: "Reading a paper",
      get_similar_papers: "Finding related work",
      compare_papers: "Comparing papers",
      explain_topic: "Looking up background",
      list_collections: "Checking collections",
      get_collection_details: "Opening a collection",
    }[name] || name;
  }

  /* ------------------------------------------------------------- sources */

  // Citations when the answer points at papers; otherwise what it read. After a
  // real search an empty panel is its own kind of lie, and the two are labelled
  // differently because they are different claims.
  function addSources(result) {
    var cited = result.citations || [];
    var consulted = result.sources || [];
    var list = cited.length ? cited : consulted;
    if (!list.length) { return; }

    var box = document.createElement("div");
    box.className = "chat-sources";

    var head = document.createElement("h3");
    head.className = "chat-sources-title";
    head.textContent = cited.length
      ? (cited.length === 1 ? "1 citation" : cited.length + " citations")
      : (consulted.length === 1 ? "1 source consulted" : consulted.length + " sources consulted");
    box.appendChild(head);

    var ol = document.createElement("ol");
    ol.className = "chat-citations";
    list.slice(0, 12).forEach(function (c) {
      var li = document.createElement("li");
      if (cited.length && c.number) { li.value = c.number; }

      var a = document.createElement("a");
      a.href = "/paper/" + c.paper_id;
      a.textContent = c.title;
      li.appendChild(a);

      var meta = citationMeta(c);
      if (meta) {
        var span = document.createElement("span");
        span.className = "chat-citation-meta";
        span.textContent = meta;
        li.appendChild(span);
      }
      ol.appendChild(li);
    });
    box.appendChild(ol);

    if (!cited.length && consulted.length > 12) {
      var more = document.createElement("p");
      more.className = "chat-sources-more";
      more.textContent = "and " + (consulted.length - 12) + " more";
      box.appendChild(more);
    }

    ensureThread().appendChild(box);
    scrollToLatest();
  }

  // Enough to judge a citation without opening it: what it is, when, where, and
  // how much it has been taken up.
  function citationMeta(c) {
    var bits = [];
    if (c.publication_year) { bits.push(String(c.publication_year)); }
    if (c.venue) { bits.push(c.venue); }
    if (typeof c.citation_count === "number") {
      bits.push(c.citation_count.toLocaleString() +
                (c.citation_count === 1 ? " citation" : " citations"));
    }
    return bits.join(" · ");
  }

  /* ---------------------------------------------------------------- send */

  function setPending(on) {
    pending = on;
    input.disabled = on;
    var send = form.querySelector(".composer-send");
    if (send) { send.disabled = on; }
  }

  function handle(event, trace, done) {
    if (event.type === "status") {
      trace.status(event.phase);
    } else if (event.type === "tool_start") {
      trace.toolStart(event.name, event.arguments);
    } else if (event.type === "tool_end") {
      trace.toolEnd(event.ok, event.found, event.error);
    } else if (event.type === "done") {
      done(event.result);
    } else if (event.type === "error") {
      trace.finish();
      addNotice(event.message);
    }
  }

  async function streamTurn(question, trace) {
    var res = await fetch("/chat/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": (document.querySelector('meta[name="csrf-token"]') || {})
          .content || "",
      },
      body: JSON.stringify({ question: question }),
    });

    // Refusals (403 capability, 429 quota, 503 unavailable) answer JSON even
    // when a stream was asked for, because there is nothing to stream.
    if (!res.ok || (res.headers.get("Content-Type") || "").indexOf("event-stream") === -1) {
      var body = null;
      try { body = await res.json(); } catch (e) { /* no body */ }
      trace.finish();
      if (body && body.status) {
        finishTurn(body, trace);
      } else {
        addNotice((body && (body.detail || body.error || body.message)) ||
                  "Request failed (" + res.status + ")");
      }
      return;
    }

    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";

    while (true) {
      var chunk = await reader.read();
      if (chunk.done) { break; }
      buffer += decoder.decode(chunk.value, { stream: true });

      // SSE frames are separated by a blank line. A frame can arrive split
      // across chunks, so only whole ones are taken and the remainder is kept.
      var frames = buffer.split("\n\n");
      buffer = frames.pop();

      frames.forEach(function (frame) {
        var line = frame.split("\n").find(function (l) { return l.indexOf("data:") === 0; });
        if (!line) { return; }
        var event;
        try { event = JSON.parse(line.slice(5).trim()); } catch (e) { return; }
        handle(event, trace, function (result) { finishTurn(result, trace); });
      });
    }
  }

  function finishTurn(result, trace) {
    trace.finish();
    if (result.answer) {
      addAnswer(result.answer);
      addSources(result);
    }
    if (result.message) { addNotice(result.message); }
    if (!result.answer && !result.message) {
      addNotice("The assistant returned nothing for that question.");
    }
  }

  async function send(question) {
    setPending(true);
    addUserMessage(question);
    input.value = "";
    autosize();

    var trace = createTrace();
    try {
      await streamTurn(question, trace);
    } catch (err) {
      trace.finish();
      addNotice(err.message || "Something went wrong.");
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
