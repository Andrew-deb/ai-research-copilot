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
  var embedded = page && page.dataset.surface === "assistant";
  var modePicker = document.getElementById("chat-mode");
  var clearContext = document.getElementById("chat-context-clear");
  if (clearContext) {
    clearContext.addEventListener("click", function () {
      page.dataset.contextKind = "";
      page.dataset.contextId = "";
      clearContext.remove();
    });
  }
  /* The mode picker is a button and a menu, not a select — a native drop-down
     is drawn by the operating system and opened as a white box inside a dark
     composer. It still exposes `.value` and still fires `change`, so the
     handler below never learns the difference. */
  var modeWrap = document.querySelector("[data-mode-picker]");
  if (modeWrap && modePicker) {
    var modeMenu = modeWrap.querySelector(".mode-pick-menu");
    var modeLabel = modePicker.querySelector(".mode-pick-value");

    var setMenu = function (open) {
      modeMenu.hidden = !open;
      modePicker.setAttribute("aria-expanded", open ? "true" : "false");
    };

    modePicker.addEventListener("click", function (e) {
      e.stopPropagation();
      setMenu(modeMenu.hidden);
    });

    modeMenu.addEventListener("click", function (e) {
      var option = e.target.closest("[role=option]");
      if (!option) { return; }

      modePicker.value = option.dataset.value;
      modeLabel.textContent = option.querySelector(".mode-pick-name").textContent;
      modeMenu.querySelectorAll("[role=option]").forEach(function (each) {
        each.setAttribute("aria-selected", each === option ? "true" : "false");
      });
      setMenu(false);
      modePicker.focus();
      // What the rest of this file already listens for.
      modePicker.dispatchEvent(new Event("change"));
    });

    document.addEventListener("click", function (e) {
      if (!modeWrap.contains(e.target)) { setMenu(false); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modeMenu.hidden) { setMenu(false); modePicker.focus(); }
    });
  }

  if (modePicker) {
    modePicker.addEventListener("change", function () {
      page.dataset.chatMode = modePicker.value;
      document.getElementById("chat-mode-description").textContent =
        modePicker.value === "wick" ? "Workspace operations" : "Paper discovery and research";
      input.placeholder = modePicker.value === "wick"
        ? "Ask Wick about your workspace…" : "Ask a research question…";
      input.setAttribute("aria-label", modePicker.value === "wick"
        ? "Ask Wick" : "Ask a research question");
      var url = new URL(location.href);
      url.searchParams.set("mode", modePicker.value);
      history.replaceState(null, "", url.pathname + url.search);
    });
  }
  var completedWrites = [];
  var writeTools = ["create_collection", "add_paper_to_collection",
    "remove_paper_from_collection", "generate_reading_plan", "mark_paper_status", "save_note", "create_note"];
  function tellParent(type, value) {
    if (embedded && window.parent !== window) {
      window.parent.postMessage({ source: "alfred-assistant", type: type, value: value }, window.location.origin);
    }
  }
  var stage = form.closest(".chat-stage") || form;
  var thread = document.getElementById("chat-thread");
  var pending = false;
  var activeRunId = null;
  var stopRequested = false;
  var stopButton = form.querySelector(".composer-stop");
  var runStorageKey = embedded
    ? "alfred-active-run:assistant:" + page.dataset.owner
    : "alfred-active-run";

  function rememberRun(id) {
    try {
      if (id) { sessionStorage.setItem(runStorageKey, id); }
      else { sessionStorage.removeItem(runStorageKey); }
    } catch (e) { /* Private browsing may block storage. */ }
  }
  var conversationId = (page && page.dataset.conversation) || null;
  var draftKey = embedded ? "alfred-panel-draft:" + page.dataset.owner : null;
  if (embedded) {
    try { if (!input.value) { input.value = sessionStorage.getItem(draftKey) || ""; } }
    catch (e) { /* private mode */ }
    input.addEventListener("input", function () {
      try { sessionStorage.setItem(draftKey, input.value); } catch (e) { /* private mode */ }
    });
  }
  function panelUrl(id) {
    var params = new URLSearchParams();
    if (id) { params.set("conversation_id", id); }
    if (page.dataset.contextKind) { params.set("context_kind", page.dataset.contextKind); }
    if (page.dataset.contextId) { params.set("context_id", page.dataset.contextId); }
    return "/chat/assistant?" + params.toString();
  }
  if (embedded) {
    window.addEventListener("message", function (event) {
      if (event.origin !== window.location.origin || event.source !== window.parent ||
          !event.data || event.data.source !== "alfred-shell") { return; }
      if (event.data.type === "focus") { input.focus(); }
      if (event.data.type === "context") {
        page.dataset.contextKind = event.data.value ? event.data.value.kind : "";
        page.dataset.contextId = event.data.value ? event.data.value.id : "";
      }
      if (event.data.type === "theme") {
        document.documentElement.dataset.theme = event.data.value === "dark" ? "dark" : "light";
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !event.defaultPrevented &&
          !document.querySelector(".chat-prompt-edit") &&
          !document.querySelector(".chat-rail.is-open") &&
          !document.querySelector(".citation-preview")) {
        tellParent("close", null);
      }
    });
    tellParent("ready", conversationId);
  }

  if (thread && page) { page.classList.add("has-conversation"); }

  /* ------------------------------------------------------------ rendering */

  function ensureThread() {
    if (thread) { return thread; }
    var welcome = stage.querySelector('.assistant-welcome');
    if (welcome) { welcome.remove(); }
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
    var bubble = document.createElement("div");
    bubble.className = "chat-prompt-text";
    bubble.textContent = text;
    el.appendChild(bubble);
    ensureThread().appendChild(el);
    scrollToLatest();
    return el;
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
      find_workspace_resources: "Finding workspace assets",
      get_workspace_resource: "Reading workspace context",
      get_workspace_paper: "Reading a saved paper",
      get_reading_progress: "Checking reading progress",
      create_note: "Creating a note",
      create_collection: "Creating a collection",
      add_paper_to_collection: "Adding a paper",
      remove_paper_from_collection: "Removing a paper",
      mark_paper_status: "Updating reading status",
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
  var expandRail = null;

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
    expandRail = function () { setCollapsed(false); };

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
      if (numbered && c.number) {
        li.value = c.number;
        li.dataset.citationNumber = String(c.number);
      }

      var a = document.createElement("a");
      a.href = "/paper/" + encodeURIComponent(c.paper_id);
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
    return group;
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

  /* Citation numbers belong to one answer. Link only numbers that its verified
     mapping contains, after Markdown has been sanitized; never parse HTML or
     turn a number from another response into a paper link. */
  var preview = null;
  var previewAnchor = null;
  var previewTimer = null;

  function dismissPreview() {
    clearTimeout(previewTimer);
    if (preview) { preview.remove(); }
    preview = null;
    previewAnchor = null;
  }

  function schedulePreviewDismissal() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(function () {
      if (preview && !preview.contains(document.activeElement) &&
          previewAnchor !== document.activeElement) { dismissPreview(); }
    }, 180);
  }

  function showSource(group, number) {
    dismissPreview();
    if (!group) { return; }
    if (window.matchMedia("(max-width: 1100px)").matches) { setDrawer(true); }
    else if (expandRail) { expandRail(); }
    var item = group.querySelector('[data-citation-number="' + number + '"]');
    if (!item) { return; }
    // Wait for the drawer to enter the viewport before scrolling its body.
    requestAnimationFrame(function () {
      item.scrollIntoView({ block: "center", behavior: "smooth" });
      item.classList.add("is-highlighted");
      var link = item.querySelector("a");
      if (link) { link.focus({ preventScroll: true }); }
      setTimeout(function () { item.classList.remove("is-highlighted"); }, 2800);
    });
  }

  function openPreview(anchor, citation, group) {
    if (previewAnchor === anchor && preview) { return; }
    dismissPreview();
    previewAnchor = anchor;
    var box = document.createElement("div");
    box.className = "chat-citation-preview";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Paper preview");

    var title = document.createElement("strong");
    title.className = "chat-preview-title";
    title.textContent = citation.title || "Paper";
    box.appendChild(title);

    var details = [];
    if (Array.isArray(citation.authors) && citation.authors.length) {
      details.push(citation.authors.slice(0, 3).map(function (author) {
        return typeof author === "string" ? author : author.display_name || author.name || "";
      }).filter(Boolean).join(", "));
    }
    if (citation.publication_year) { details.push(String(citation.publication_year)); }
    if (details.length) {
      var meta = document.createElement("span");
      meta.className = "chat-preview-meta";
      meta.textContent = details.join(" · ");
      box.appendChild(meta);
    }

    var excerpt = document.createElement("p");
    excerpt.className = "chat-preview-excerpt";
    var summary = citation.tldr || citation.abstract_excerpt;
    excerpt.textContent = summary ? String(summary) : "No summary available for this paper.";
    box.appendChild(excerpt);
    if (summary) {
      var kind = document.createElement("span");
      kind.className = "chat-preview-kind";
      kind.textContent = citation.tldr ? "Source summary" : "Abstract excerpt";
      box.insertBefore(kind, excerpt);
    }

    var actions = document.createElement("div");
    actions.className = "chat-preview-actions";
    var open = document.createElement("a");
    open.href = anchor.href;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    open.textContent = "Open paper";
    actions.appendChild(open);
    if (group) {
      var reveal = document.createElement("button");
      reveal.type = "button";
      reveal.textContent = "Show in sources";
      reveal.addEventListener("click", function () { showSource(group, citation.number); });
      actions.appendChild(reveal);
    }
    var close = document.createElement("button");
    close.type = "button";
    close.className = "chat-preview-close";
    close.setAttribute("aria-label", "Close paper preview");
    close.textContent = "×";
    close.addEventListener("click", function () { dismissPreview(); anchor.focus(); });
    box.appendChild(actions);
    box.appendChild(close);
    box.addEventListener("mouseenter", function () { clearTimeout(previewTimer); });
    box.addEventListener("mouseleave", schedulePreviewDismissal);
    box.addEventListener("focusout", schedulePreviewDismissal);
    document.body.appendChild(box);
    preview = box;

    var rect = anchor.getBoundingClientRect();
    var width = box.offsetWidth;
    var height = box.offsetHeight;
    box.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + "px";
    box.style.top = (rect.bottom + height + 8 <= window.innerHeight
      ? rect.bottom + 6 : Math.max(8, rect.top - height - 6)) + "px";
  }

  function linkCitations(answer, citations, group) {
    if (!answer || !Array.isArray(citations) || !citations.length) { return; }
    var mapped = Object.create(null);
    citations.forEach(function (c) {
      if (Number.isSafeInteger(Number(c.number)) && Number(c.number) > 0 &&
          c.paper_id && c.title) { mapped[String(c.number)] = c; }
    });
    var walker = document.createTreeWalker(answer, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (node.parentElement.closest("a, code, pre, button")) {
          return NodeFilter.FILTER_REJECT;
        }
        return /\[\d+\]/.test(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    var nodes = [];
    while (walker.nextNode()) { nodes.push(walker.currentNode); }
    nodes.forEach(function (node) {
      var text = node.nodeValue;
      var re = /\[(\d+)\]/g;
      var match, start = 0, fragment = document.createDocumentFragment();
      while ((match = re.exec(text))) {
        let citation = mapped[String(Number(match[1]))];
        if (!citation) { continue; }
        fragment.appendChild(document.createTextNode(text.slice(start, match.index)));
        let link = document.createElement("a");
        link.className = "chat-inline-citation";
        link.href = "/paper/" + encodeURIComponent(citation.paper_id);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = match[0];
        link.setAttribute("aria-label", "Paper " + citation.number + ": " + citation.title);
        link.addEventListener("mouseenter", function () { openPreview(link, citation, group); });
        link.addEventListener("mouseleave", schedulePreviewDismissal);
        link.addEventListener("focus", function () { openPreview(link, citation, group); });
        link.addEventListener("blur", schedulePreviewDismissal);
        link.addEventListener("click", function (event) {
          if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
            event.preventDefault();
            openPreview(link, citation, group);
            if (preview) { preview.querySelector(".chat-preview-close").focus(); }
          }
        });
        fragment.appendChild(link);
        start = re.lastIndex;
      }
      if (start) {
        fragment.appendChild(document.createTextNode(text.slice(start)));
        node.replaceWith(fragment);
      }
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && preview) { dismissPreview(); }
  });
  document.addEventListener("pointerdown", function (event) {
    if (preview && !preview.contains(event.target) && event.target !== previewAnchor) {
      dismissPreview();
    }
  });
  window.addEventListener("scroll", dismissPreview, true);
  window.addEventListener("resize", dismissPreview);

  /* ---------------------------------------------------------------- send */

  function setPending(on) {
    pending = on;
    input.disabled = on;
    var send = form.querySelector(".composer-send");
    if (send) { send.hidden = on; send.disabled = on; }
    if (stopButton) {
      stopButton.hidden = !on;
      stopButton.disabled = !on || stopRequested;
      stopButton.textContent = stopRequested ? "Stopping…" : "Stop";
    }
  }

  if (stopButton) {
    stopButton.addEventListener("click", async function () {
      if (!pending || stopRequested) { return; }
      stopRequested = true;
      setPending(true);
      // The run event normally arrives before the first step. A press during
      // that short gap is sent as soon as the server supplies its identifier.
      if (activeRunId) { await requestStop(activeRunId); }
    });
  }

  async function requestStop(runId) {
    try {
      var res = await fetch("/chat/runs/" + encodeURIComponent(runId) + "/stop", {
        method: "POST",
        headers: { "X-CSRFToken": (document.querySelector('meta[name="csrf-token"]') || {})
          .content || "", "X-Requested-With": "XMLHttpRequest" },
      });
      if (!res.ok) { throw new Error("Could not request a stop."); }
      var result = await res.json();
      if (result.state === "completed") { stopButton.textContent = "Finishing…"; }
    } catch (err) {
      addNotice(err.message || "Could not request a stop.");
      stopRequested = false;
      setPending(true);
    }
  }

  function handle(event, trace, done) {
    if (event.type === "run") {
      activeRunId = event.run_id;
      rememberRun(activeRunId);
      if (stopRequested) { requestStop(activeRunId); }
    } else if (event.type === "conversation" && embedded && event.conversation_id) {
      conversationId = event.conversation_id;
      tellParent("conversation", conversationId);
    } else if (event.type === "status") {
      if (stopRequested) { return; }
      trace.status(event.phase);
    } else if (event.type === "tool_start") {
      trace.toolStart(event.name, event.arguments);
    } else if (event.type === "tool_end") {
      trace.toolEnd(event.ok, event.found, event.error, event.label);
      if (embedded && event.ok && writeTools.indexOf(event.name) !== -1) {
        completedWrites.push(event.name);
      }
    } else if (event.type === "done") {
      done(event.result);
    } else if (event.type === "error") {
      trace.finish();
      addNotice(event.message);
    }
  }

  async function streamTurn(question, trace, options) {
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
      body: JSON.stringify({ question: question, conversation_id: conversationId,
        action: options.action || "new", source_message_id: options.source_message_id || null,
        surface: embedded ? "assistant" : "agent",
        chat_mode: embedded ? "wick" : (modePicker ? modePicker.value : "research"),
        context_kind: (embedded || (page && page.dataset.chatMode === "wick")) ? page.dataset.contextKind : "",
        context_id: (embedded || (page && page.dataset.chatMode === "wick")) ? page.dataset.contextId : "" }),
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
        handle(event, trace, function (result) {
          finishTurn(result, trace, question);
          if (embedded && completedWrites.length) {
            tellParent("writes", completedWrites.slice());
            completedWrites = [];
          }
          if (options.action && result.conversation_id && result.answer) {
            window.location.href = embedded
              ? panelUrl(result.conversation_id)
              : "/chat/" + encodeURIComponent(result.conversation_id);
          }
        });
      });
    }
  }

  function rememberConversation(result, question) {
    if (!result || !result.conversation_id) { return; }
    var isNew = !conversationId;
    conversationId = result.conversation_id;
    if (embedded) {
      tellParent("conversation", conversationId);
      return;
    }

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
    var section = document.getElementById("nav-history-results");
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

    section.prepend(link);
  }

  function finishTurn(result, trace, question) {
    trace.finish();
    rememberConversation(result, question);
    if (result.answer) {
      var answer = addAnswer(result.answer);
      var sources = addSources(result, question);
      linkCitations(answer, result.citations, sources);
    } else if (result.status === "stopped") {
      addSources(result, question);
    }
    if (result.message) { addNotice(result.message); }
    if (!result.answer && !result.message) {
      addNotice("The assistant returned nothing for that question.");
    }
  }

  async function send(question, options) {
    options = options || {};
    if (pending) { return; }
    completedWrites = [];
    activeRunId = null;
    stopRequested = false;
    setPending(true);
    if (options.action !== "regenerate") { addUserMessage(question); }
    if (!options.action) { input.value = ""; }
    if (embedded && !options.action) {
      try { sessionStorage.removeItem(draftKey); } catch (e) { /* private mode */ }
    }
    autosize();

    var trace = createTrace();
    try {
      await streamTurn(question, trace, options);
    } catch (err) {
      trace.finish();
      addNotice(activeRunId
        ? "The connection ended. The run may still be active; check its status before retrying."
        : (err.message || "Something went wrong."));
    } finally {
      var detached = false;
      if (activeRunId) {
        try {
          var status = await fetch("/chat/runs/" + encodeURIComponent(activeRunId));
          var state = status.ok ? (await status.json()).state : null;
          if (state && state !== "running" && state !== "stop_requested") {
            rememberRun(null);
          } else if (state === "running" || state === "stop_requested") {
            detached = true;
            recoverRun(activeRunId);
          }
        } catch (e) {
          detached = true;
          recoverRun(activeRunId);
        }
      }
      if (!detached) {
        activeRunId = null;
        stopRequested = false;
        setPending(false);
        input.focus();
      }
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

  function versionControls(el, message, prompt) {
    if (!conversationId || !message.message_id) { return; }
    var bar = document.createElement("div");
    bar.className = "chat-version-actions";
    if (message.role === "user") {
      var edit = document.createElement("button");
      edit.type = "button";
      edit.className = "chat-action-icon chat-action-edit";
      edit.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L9 17l-4 1 1-4L16.5 3.5Z"/></svg>';
      edit.title = "Edit prompt";
      edit.setAttribute("aria-label", "Edit this prompt and create a new version");
      edit.addEventListener("click", function () {
        if (pending || el.querySelector(".chat-prompt-edit")) { return; }
        var bubble = el.querySelector(".chat-prompt-text");
        var formEdit = document.createElement("div");
        formEdit.className = "chat-prompt-edit";
        var field = document.createElement("textarea");
        field.value = message.content;
        field.maxLength = 2000;
        field.setAttribute("aria-label", "Edit prompt");
        var save = document.createElement("button");
        save.type = "button";
        save.className = "chat-prompt-save";
        save.textContent = "Send";
        save.addEventListener("click", function () {
          var changed = field.value.trim();
          if (changed) {
            closeEditor();
            send(changed, { action: "edit", source_message_id: message.message_id });
          }
        });
        var cancel = document.createElement("button");
        cancel.type = "button";
        cancel.textContent = "Cancel";
        function closeEditor() {
          formEdit.remove();
          bubble.hidden = false;
          bar.hidden = false;
          el.classList.remove("is-editing");
        }
        cancel.addEventListener("click", closeEditor);
        field.addEventListener("keydown", function (event) {
          if (event.key === "Escape") {
            event.preventDefault(); event.stopPropagation(); closeEditor(); edit.focus();
          }
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { save.click(); }
        });
        var buttons = document.createElement("div");
        buttons.className = "chat-prompt-edit-actions";
        buttons.appendChild(cancel);
        buttons.appendChild(save);
        formEdit.appendChild(field);
        formEdit.appendChild(buttons);
        bubble.hidden = true;
        bar.hidden = true;
        el.classList.add("is-editing");
        el.appendChild(formEdit);
        field.focus();
        field.setSelectionRange(field.value.length, field.value.length);
      });
      bar.appendChild(edit);
    } else if (prompt && prompt.message_id) {
      var regen = document.createElement("button");
      regen.type = "button";
      regen.className = "chat-action-icon chat-action-regen";
      regen.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 6.3M20 4v7h-7"/></svg>';
      regen.title = "Regenerate answer";
      regen.setAttribute("aria-label", "Regenerate answer and keep this version");
      regen.addEventListener("click", function () {
        if (!pending) {
          send(prompt.content, { action: "regenerate",
                                 source_message_id: prompt.message_id });
        }
      });
      bar.appendChild(regen);
    }
    var versions = message.versions || [];
    if (versions.length > 1) {
      var index = versions.indexOf(message.message_id);
      var position = document.createElement("span");
      position.textContent = (index + 1) + " / " + versions.length;
      var move = function (direction) {
        var target = versions[index + direction];
        if (!target || pending) { return; }
        fetch("/chat/" + encodeURIComponent(conversationId) + "/versions/" +
              encodeURIComponent(target) + "/select", {
          method: "POST",
          headers: { "X-CSRFToken": (document.querySelector('meta[name="csrf-token"]') || {})
            .content || "", "X-Requested-With": "XMLHttpRequest" },
        }).then(function (res) {
          if (!res.ok) { throw new Error(); }
          window.location.reload();
        }).catch(function () { addNotice("Could not switch versions."); });
      };
      var previous = document.createElement("button");
      previous.type = "button";
      previous.textContent = "‹";
      previous.disabled = index <= 0;
      previous.setAttribute("aria-label", "Previous version");
      previous.addEventListener("click", function () { move(-1); });
      var next = document.createElement("button");
      next.type = "button";
      next.textContent = "›";
      next.disabled = index >= versions.length - 1;
      next.setAttribute("aria-label", "Next version");
      next.addEventListener("click", function () { move(1); });
      bar.appendChild(previous);
      bar.appendChild(position);
      bar.appendChild(next);
    }
    if (bar.childNodes.length) { el.appendChild(bar); }
  }

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

    var previousPrompt = null;
    history.forEach(function (message) {
      if (message.role === "user") {
        var promptEl = addUserMessage(message.content || "");
        versionControls(promptEl, message, null);
        previousPrompt = message;
      } else if (message.role === "assistant") {
        replayTrace(message);
        var answer = message.content ? addAnswer(message.content) : null;
        if (answer) { versionControls(answer, message, previousPrompt); }
        var sources = addSources(message, "");
        linkCitations(answer, message.citations, sources);
      } else {
        addNotice(message.content || "");
      }
    });
  }

  replay();

  // A disconnected SSE connection does not cancel the worker. On reload, ask
  // the shared run ledger what happened and offer Stop if it is still running.
  var pendingNoticeShown = false;
  async function recoverRun(id) {
    if (activeRunId !== id) { return; }
    try {
      var res = await fetch("/chat/runs/" + encodeURIComponent(id));
      if (!res.ok) {
        rememberRun(null);
        activeRunId = null;
        stopRequested = false;
        setPending(false);
        return;
      }
      var state = (await res.json()).state;
      if (state === "running" || state === "stop_requested") {
        stopRequested = state === "stop_requested";
        setPending(true);
        if (!pendingNoticeShown) {
          addNotice("A previous research run is still " +
                    (stopRequested ? "stopping." : "working. You can stop it here."));
          pendingNoticeShown = true;
        }
        setTimeout(function () { recoverRun(id); }, 2000);
        return;
      }
      rememberRun(null);
      activeRunId = null;
      stopRequested = false;
      setPending(false);
      addNotice(state === "completed"
        ? "Your previous run finished. Reload your conversations to see its answer."
        : state === "stopped" ? "Previous run stopped." : "Previous run ended.");
      if (embedded && state === "completed" && conversationId) {
        window.location.replace(panelUrl(conversationId));
      }
    } catch (e) {
      if (!pendingNoticeShown) {
        addNotice("Could not check the previous run status. Retrying…");
        pendingNoticeShown = true;
      }
      setTimeout(function () { recoverRun(id); }, 4000);
    }
  }
  try { activeRunId = sessionStorage.getItem(runStorageKey); } catch (e) { /* ignore */ }
  if (activeRunId) { recoverRun(activeRunId); }

  // A question may arrive already in the box via ?q=. Size it to what it holds
  // and put the caret at the end, so it reads as something to edit rather than
  // placeholder text sitting in the way.
  if (input.value.trim()) {
    autosize();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
})();
