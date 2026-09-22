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
  var conversationId = (page && page.dataset.conversation) || null;

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
    // Built now rather than when the first source arrives: the wrapper is what
    // gives the column its fixed height, and without it an answer that used no
    // tools would still grow the page and push the composer out of sight.
    ensureRail();
    return thread;
  }

  function scrollToLatest() {
    if (!thread) { return; }
    // The thread scrolls, not the document. When the page scrolled instead,
    // a long answer pushed the composer below the fold and you had to scroll
    // back down to type the next question.
    thread.scrollTop = thread.scrollHeight;
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
        current.dataset.tool = name;
        // A query or a topic is worth showing. A raw identifier is not: a
        // 36-character UUID told the reader nothing, wrapped onto four lines
        // and dragged a horizontal scrollbar across the conversation. The
        // title arrives on tool_end anyway, which is the part worth waiting
        // for.
        var what = (args && (args.query || args.topic)) || "";
        if (!what && args) {
          var id = args.paper_id || args.paper_id_or_doi || "";
          if (id && !IDENTIFIER.test(id)) { what = id; }
        }
        current.textContent = prettyTool(name) + (what ? " · " + what : "");
        steps.appendChild(current);
        setSummary(prettyTool(name) + "…");
        scrollToLatest();
      },
      toolEnd: function (ok, found, error, label) {
        if (!current) { return; }
        current.classList.remove("is-running");
        current.classList.add(ok ? "is-done" : "is-failed");
        // The title, once the call has returned — better than an opaque id,
        // and only knowable after the fact. The tool name stays as the prefix
        // so the step still says what kind of work it was.
        if (ok && label) {
          current.textContent = current.dataset.tool
            ? prettyTool(current.dataset.tool) + " · " + label
            : label;
        }
        if (ok && found) {
          counts.papers += found;
          var n = document.createElement("span");
          n.className = "chat-step-count";
          n.textContent = found + (found === 1 ? " paper" : " papers");
          current.appendChild(n);
        } else if (!ok && error) {
          var e = document.createElement("span");
          e.className = "chat-step-error";
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

  // A UUID, or a DOI. Both are addresses, not names.
  var IDENTIFIER = /^[0-9a-f-]{20,}$|^10\.\d{4,}\//i;

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

  /* --------------------------------------------------------------- rail */

  // Sources live in a panel beside the answer, not underneath it. Inline, they
  // interrupted the reading: a list of twenty papers between one answer and the
  // next question is a wall the eye has to climb over to follow the
  // conversation. Beside it, they are there when wanted and quiet when not.
  //
  // Built here rather than in the template because both surfaces that run the
  // composer need it, and neither should have to know about the other's markup.
  var rail = null;
  var railLauncher = null;
  var railScrim = null;
  var sourceCount = 0;

  function setDrawer(open) {
    if (!rail) { return; }
    rail.classList.toggle("is-open", open);
    if (railScrim) { railScrim.hidden = !open; }
    // Only while the drawer covers the page — a scroll lock left on would
    // freeze the conversation behind it.
    document.body.classList.toggle("rail-open", open);
  }

  // Deliberately not remembered between visits: it starts where it belongs, and
  // moving it is a response to what is on screen right now — a position saved
  // from yesterday would be in the way of something else today.
  var DRAG_THRESHOLD = 6;

  function makeDraggable(el, onTap) {
    var startX = 0, startY = 0, originX = 0, originY = 0, moved = false;

    function clamp(x, y) {
      var margin = 8;
      return {
        x: Math.max(margin, Math.min(x, window.innerWidth - el.offsetWidth - margin)),
        y: Math.max(margin, Math.min(y, window.innerHeight - el.offsetHeight - margin)),
      };
    }

    el.addEventListener("pointerdown", function (e) {
      var box = el.getBoundingClientRect();
      startX = e.clientX;
      startY = e.clientY;
      originX = box.left;
      originY = box.top;
      moved = false;
      el.setPointerCapture(e.pointerId);
    });

    el.addEventListener("pointermove", function (e) {
      if (!el.hasPointerCapture(e.pointerId)) { return; }
      var dx = e.clientX - startX;
      var dy = e.clientY - startY;
      // A press that has not travelled is still a tap; only past the threshold
      // does it become a drag, or the button would be impossible to press.
      if (!moved && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) {
        return;
      }
      moved = true;
      var next = clamp(originX + dx, originY + dy);
      // Switching to left/top once dragged; it starts anchored right/bottom.
      el.style.left = next.x + "px";
      el.style.top = next.y + "px";
      el.style.right = "auto";
      el.style.bottom = "auto";
    });

    el.addEventListener("pointerup", function (e) {
      if (el.hasPointerCapture(e.pointerId)) { el.releasePointerCapture(e.pointerId); }
      if (!moved) { onTap(); }
    });

    // A button parked against an edge would be half off-screen after a rotate.
    window.addEventListener("resize", function () {
      if (el.style.left === "" || el.hidden) { return; }
      var box = el.getBoundingClientRect();
      var next = clamp(box.left, box.top);
      el.style.left = next.x + "px";
      el.style.top = next.y + "px";
    });
  }

  function updateLauncher() {
    if (!railLauncher) { return; }
    railLauncher.hidden = sourceCount === 0;
    railLauncher.textContent = "Sources" + (sourceCount ? " (" + sourceCount + ")" : "");
  }

  function ensureRail() {
    if (rail) { return rail; }

    var wrapper = document.createElement("div");
    wrapper.className = "chat-with-rail";
    page.parentNode.insertBefore(wrapper, page);
    wrapper.appendChild(page);

    rail = document.createElement("aside");
    rail.className = "chat-rail";
    rail.setAttribute("aria-label", "Sources");

    // Opens and closes on every screen, not just narrow ones. Sources are
    // worth having beside the answer and not worth a third of the width when
    // you are reading rather than checking.
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "chat-rail-toggle";
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-controls", "chat-rail-body");

    var label = document.createElement("span");
    label.className = "chat-rail-label";
    label.textContent = "Sources";
    toggle.appendChild(label);

    function setCollapsed(collapsed) {
      rail.classList.toggle("is-collapsed", collapsed);
      wrapper.classList.toggle("rail-collapsed", collapsed);
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      toggle.title = collapsed ? "Show sources" : "Hide sources";
      // Per-viewer convenience only, and never allowed to break the page:
      // storage throws in a private window and returns nothing after a clear.
      try { localStorage.setItem("rc-rail-collapsed", collapsed ? "1" : "0"); }
      catch (e) { /* ignore */ }
    }

    toggle.addEventListener("click", function () {
      // The same control means "close" in a drawer and "collapse" in a column.
      if (rail.classList.contains("is-open")) { setDrawer(false); }
      else { setCollapsed(!rail.classList.contains("is-collapsed")); }
    });
    rail.appendChild(toggle);

    var body = document.createElement("div");
    body.className = "chat-rail-body";
    body.id = "chat-rail-body";
    rail.appendChild(body);

    // Hidden until it has something to show. A "Sources" heading over an empty
    // column is a promise the turn has not kept yet.
    rail.classList.add("is-empty");

    wrapper.appendChild(rail);

    // Below the layout breakpoint the rail is a drawer rather than a column,
    // opened the same way the navigation sidebar is. Stacked under the
    // composer it was simply out of sight: nobody scrolls past the thing they
    // are typing into to find the sources.
    var scrim = document.createElement("div");
    scrim.className = "chat-rail-scrim";
    scrim.hidden = true;
    document.body.appendChild(scrim);

    var launcher = document.createElement("button");
    launcher.type = "button";
    launcher.className = "chat-rail-launcher";
    launcher.hidden = true;                  // until there is something to show
    launcher.setAttribute("aria-controls", "chat-rail-body");
    launcher.title = "Sources — drag to move";
    document.body.appendChild(launcher);

    makeDraggable(launcher, function () { setDrawer(true); });

    railLauncher = launcher;
    railScrim = scrim;

    scrim.addEventListener("click", function () { setDrawer(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { setDrawer(false); }
    });

    var remembered = null;
    try { remembered = localStorage.getItem("rc-rail-collapsed"); } catch (e) { /* ignore */ }
    setCollapsed(remembered === "1");
    return rail;
  }

  function railBody() {
    return ensureRail().querySelector(".chat-rail-body");
  }

  function sourceList(items, numbered) {
    var ol = document.createElement("ol");
    ol.className = "chat-citations" + (numbered ? "" : " is-plain");
    items.forEach(function (c) {
      var li = document.createElement("li");
      if (numbered && c.number) { li.value = c.number; }

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
    return ol;
  }

  function section(title, note, items, numbered) {
    var box = document.createElement("section");
    box.className = "chat-rail-section";

    var h = document.createElement("h3");
    h.className = "chat-rail-heading";
    h.textContent = title + " (" + items.length + ")";
    box.appendChild(h);

    if (note) {
      var p = document.createElement("p");
      p.className = "chat-rail-note";
      p.textContent = note;
      box.appendChild(p);
    }

    box.appendChild(sourceList(items, numbered));
    return box;
  }

  // Two different claims, kept apart on purpose. A citation is a paper the
  // answer points at through the verified mapping; a source is one the agent
  // read on the way. Merging them would either inflate the citation list with
  // papers the prose never used, or hide the work behind an answer.
  function addSources(result, question) {
    var cited = result.citations || [];
    var citedIds = {};
    cited.forEach(function (c) { citedIds[c.paper_id] = true; });

    var consulted = (result.sources || []).filter(function (s) {
      return !citedIds[s.paper_id];
    });
    if (!cited.length && !consulted.length) { return; }

    var group = document.createElement("div");
    group.className = "chat-rail-group";

    if (question) {
      var label = document.createElement("p");
      label.className = "chat-rail-question";
      label.textContent = question;
      group.appendChild(label);
    }

    if (cited.length) {
      group.appendChild(section("Citations",
        "Referenced by the answer.", cited, true));
    }
    if (consulted.length) {
      group.appendChild(section("Sources consulted",
        "Read during the search, not cited.", consulted, false));
    }

    var body = railBody();
    body.appendChild(group);
    rail.classList.remove("is-empty");
    sourceCount += cited.length || consulted.length;
    updateLauncher();
  }

  // Enough to judge a source without opening it: what it is, when, where, and
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
      trace.toolEnd(event.ok, event.found, event.error, event.label);
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
      // The id travels with every turn so the second question lands in the same
      // conversation as the first, rather than starting a new one each time.
      body: JSON.stringify({ question: question, conversation_id: conversationId }),
    });

    // Refusals (403 capability, 429 quota, 503 unavailable) answer JSON even
    // when a stream was asked for, because there is nothing to stream.
    if (!res.ok || (res.headers.get("Content-Type") || "").indexOf("event-stream") === -1) {
      var body = null;
      try { body = await res.json(); } catch (e) { /* no body */ }
      trace.finish();
      if (body && body.status) {
        finishTurn(body, trace, question);
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
        handle(event, trace, function (result) { finishTurn(result, trace, question); });
      });
    }
  }

  function rememberConversation(result, question) {
    if (!result || !result.conversation_id) { return; }
    var isNew = !conversationId;
    conversationId = result.conversation_id;

    // Replace rather than push: the question has already been asked, so a back
    // button that returned to the empty page would undo nothing and confuse.
    try {
      window.history.replaceState({}, "", "/chat/" + conversationId);
    } catch (e) { /* ignore */ }

    if (isNew) { addToSidebar(conversationId, question); }
  }

  // The sidebar is rendered server-side, so a conversation started in this tab
  // would otherwise not appear until the next full page load.
  function addToSidebar(id, question) {
    var section = document.querySelector(".nav-section-scroll");
    if (!section) { return; }

    var empty = section.querySelector(".nav-empty");
    if (empty) { empty.remove(); }

    var link = document.createElement("a");
    link.href = "/chat/" + id;
    link.className = "nav-item nav-item-chat active";
    var icon = section.querySelector(".nav-item-chat svg");
    if (icon) { link.appendChild(icon.cloneNode(true)); }
    var label = document.createElement("span");
    label.textContent = question.length > 60
      ? question.slice(0, 60).replace(/\s+\S*$/, "") + "…"
      : question;
    link.appendChild(label);

    var heading = section.querySelector(".nav-label");
    if (heading && heading.nextSibling) {
      section.insertBefore(link, heading.nextSibling);
    } else {
      section.appendChild(link);
    }
  }

  function finishTurn(result, trace, question) {
    trace.finish();
    rememberConversation(result, question);
    if (result.answer) {
      addAnswer(result.answer);
      addSources(result, question);
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

  /* -------------------------------------------------------------- replay */

  // A stored turn is drawn by the same functions a live one uses. That is the
  // whole design: one renderer means a reopened answer cannot drift from the
  // answer that was originally given.
  function replayTrace(message) {
    var calls = message.tool_calls || [];
    var usage = message.usage || {};
    if (!calls.length) { return; }

    var box = document.createElement("div");
    box.className = "chat-trace";

    var summary = document.createElement("button");
    summary.type = "button";
    summary.className = "chat-trace-summary";
    summary.setAttribute("aria-expanded", "false");
    var bits = [calls.length + (calls.length === 1 ? " step" : " steps")];
    if (usage.llm_turns) {
      bits.push(usage.llm_turns + (usage.llm_turns === 1 ? " turn" : " turns"));
    }
    summary.textContent = bits.join(" · ");

    var steps = document.createElement("ol");
    steps.className = "chat-trace-steps";
    calls.forEach(function (call) {
      var li = document.createElement("li");
      li.className = "chat-step " + (call.ok ? "is-done" : "is-failed");
      var args = call.arguments || {};
      var what = args.query || args.topic || "";
      li.textContent = prettyTool(call.name) + (what ? " · " + what : "");
      steps.appendChild(li);
    });

    summary.addEventListener("click", function () {
      var open = box.classList.toggle("is-open");
      summary.setAttribute("aria-expanded", open ? "true" : "false");
    });

    box.appendChild(summary);
    box.appendChild(steps);
    ensureThread().appendChild(box);
  }

  function replay() {
    var tag = document.getElementById("chat-history");
    if (!tag) { return; }

    var history;
    try { history = JSON.parse(tag.textContent); } catch (e) { return; }
    if (!history || !history.length) { return; }

    history.forEach(function (message) {
      if (message.role === "user") {
        addUserMessage(message.content || "");
      } else if (message.role === "assistant") {
        replayTrace(message);
        if (message.content) { addAnswer(message.content); }
        addSources(message, "");
      } else {
        addNotice(message.content || "");
      }
    });
  }

  replay();

  // A question may arrive already in the box via ?q=. Size it to what it holds
  // and put the caret at the end, so it reads as something to edit rather than
  // placeholder text sitting in the way.
  if (input.value.trim()) {
    autosize();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
})();
