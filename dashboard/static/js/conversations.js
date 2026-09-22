/* conversations.js — the sidebar's conversation list.

   Pin, rename and delete, from a menu shared by every row. Loaded only for
   signed-in visitors, because anonymous sessions have no history to manage.

   One menu element is moved to whichever row opened it rather than one popover
   per conversation: twelve rows would otherwise mean twelve identical menus in
   the DOM, eleven of which can never be open. */

(function () {
  "use strict";

  var list = document.querySelector(".nav-section-scroll");
  var menu = document.getElementById("chat-menu");
  if (!list || !menu) { return; }

  var openFor = null;   // the row the menu currently belongs to

  function csrf() {
    var tag = document.querySelector('meta[name="csrf-token"]');
    return tag ? tag.getAttribute("content") : "";
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrf(),
      },
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      if (!res.ok) { throw new Error("Request failed (" + res.status + ")"); }
      return res.status === 204 ? null : res.json();
    });
  }

  /* ----------------------------------------------------------------- menu */

  function closeMenu() {
    menu.hidden = true;
    if (openFor) {
      var button = openFor.querySelector(".nav-chat-menu");
      if (button) { button.setAttribute("aria-expanded", "false"); }
    }
    openFor = null;
  }

  function openMenu(row, button) {
    openFor = row;
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");

    // Pin and Unpin are the same control; the label says which way it will go.
    var label = menu.querySelector('[data-label="pin"]');
    if (label) {
      label.textContent = row.dataset.pinned === "1" ? "Unpin" : "Pin";
    }

    // Positioned against the viewport because the sidebar scrolls: anchored
    // inside it, the menu would scroll away from the row it belongs to.
    var box = button.getBoundingClientRect();
    menu.style.top = Math.min(box.bottom + 4,
                              window.innerHeight - menu.offsetHeight - 8) + "px";
    menu.style.left = Math.min(box.left,
                               window.innerWidth - menu.offsetWidth - 8) + "px";

    var first = menu.querySelector("[data-action]");
    if (first) { first.focus(); }
  }

  /* --------------------------------------------------------------- rename */

  // Edited in place. A dialog for one short string is a heavier interaction
  // than the change deserves, and the row is where the name is read.
  function startRename(row) {
    var titleEl = row.querySelector(".nav-chat-title");
    if (!titleEl || row.querySelector(".nav-chat-rename")) { return; }

    var original = titleEl.textContent;
    var input = document.createElement("input");
    input.type = "text";
    input.className = "nav-chat-rename";
    input.value = original;
    input.maxLength = 60;

    titleEl.replaceWith(input);
    input.focus();
    input.select();

    var settled = false;

    function restore(text) {
      if (settled) { return; }
      settled = true;
      var span = document.createElement("span");
      span.className = "nav-chat-title";
      span.textContent = text;
      input.replaceWith(span);
    }

    function commit() {
      var next = input.value.trim();
      // Nothing typed, or nothing changed: put the old name back rather than
      // sending a request that would either fail or do nothing.
      if (!next || next === original) { return restore(original); }

      restore(next);
      post("/chat/" + row.dataset.conversation + "/rename", { title: next })
        .then(function (data) {
          var span = row.querySelector(".nav-chat-title");
          // The server trims and caps, so the stored name is the truth.
          if (span && data && data.title) { span.textContent = data.title; }
        })
        .catch(function () {
          var span = row.querySelector(".nav-chat-title");
          if (span) { span.textContent = original; }
          if (window.RC) { window.RC.toast("Could not rename that conversation.", "danger"); }
        });
    }

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "Escape") { e.preventDefault(); restore(original); }
    });
    input.addEventListener("blur", commit);
  }

  /* -------------------------------------------------------------- actions */

  function togglePin(row) {
    var wanted = row.dataset.pinned !== "1";
    post("/chat/" + row.dataset.conversation + "/pin", { pinned: wanted })
      .then(function () {
        row.dataset.pinned = wanted ? "1" : "0";
        row.classList.toggle("pinned", wanted);
        // The list is ordered by the server, so the new position only shows on
        // the next render. Saying so beats silently doing nothing visible.
        if (window.RC) {
          window.RC.toast(wanted ? "Pinned. It moves to the top on your next visit."
                                 : "Unpinned.", "success");
        }
      })
      .catch(function () {
        if (window.RC) { window.RC.toast("Could not pin that conversation.", "danger"); }
      });
  }

  function remove(row) {
    if (!window.confirm("Delete this conversation? This cannot be undone.")) { return; }
    post("/chat/" + row.dataset.conversation + "/delete")
      .then(function () {
        var active = row.classList.contains("active");
        row.remove();
        // Standing on a page that no longer exists — go somewhere that does.
        if (active) { window.location.href = "/chat"; }
      })
      .catch(function () {
        if (window.RC) { window.RC.toast("Could not delete that conversation.", "danger"); }
      });
  }

  /* --------------------------------------------------------------- wiring */

  list.addEventListener("click", function (e) {
    var button = e.target.closest(".nav-chat-menu");
    if (!button) { return; }
    e.preventDefault();
    var row = button.closest(".nav-chat-row");
    if (openFor === row) { closeMenu(); } else { closeMenu(); openMenu(row, button); }
  });

  // Double-click the name to rename, the shortcut for the common case.
  list.addEventListener("dblclick", function (e) {
    var title = e.target.closest(".nav-chat-title");
    if (!title) { return; }
    e.preventDefault();
    startRename(title.closest(".nav-chat-row"));
  });

  menu.addEventListener("click", function (e) {
    var item = e.target.closest("[data-action]");
    if (!item || !openFor) { return; }
    var row = openFor;
    closeMenu();

    if (item.dataset.action === "pin") { togglePin(row); }
    else if (item.dataset.action === "rename") { startRename(row); }
    else if (item.dataset.action === "delete") { remove(row); }
  });

  document.addEventListener("click", function (e) {
    if (menu.hidden) { return; }
    if (!e.target.closest("#chat-menu") && !e.target.closest(".nav-chat-menu")) {
      closeMenu();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !menu.hidden) { closeMenu(); }
  });

  // A menu positioned against the viewport has to close when the viewport
  // moves, or it detaches from the row it belongs to.
  window.addEventListener("resize", closeMenu);
  window.addEventListener("scroll", closeMenu, true);
})();
