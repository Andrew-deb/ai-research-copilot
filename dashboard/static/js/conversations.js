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
  //
  // The input is a SIBLING of the link, never inside it. Nested, every click
  // and every keystroke bubbled to the anchor and navigated away — which is
  // why renaming appeared to "refresh the page" and could not be completed.
  function startRename(row) {
    var link = row.querySelector(".nav-item-chat");
    var titleEl = row.querySelector(".nav-chat-title");
    if (!link || !titleEl || row.querySelector(".nav-chat-edit")) { return; }

    var original = titleEl.textContent;

    // The row keeps its shape: same icon, same position, same type — only the
    // title becomes editable. A bordered box dropped into the row read as a
    // form bolted on beside the name rather than the name itself being edited.
    var edit = document.createElement("span");
    edit.className = "nav-chat-edit";

    var icon = link.querySelector("svg");
    if (icon) { edit.appendChild(icon.cloneNode(true)); }

    var input = document.createElement("input");
    input.type = "text";
    input.className = "nav-chat-rename";
    input.value = original;
    input.maxLength = 60;
    input.setAttribute("aria-label", "Conversation name");
    edit.appendChild(input);

    // A class, not the `hidden` attribute: `.nav-item` declares `display: flex`,
    // which beats the browser's own `[hidden] { display: none }` — so the old
    // title stayed on screen beside the field being typed into.
    link.classList.add("is-editing");
    row.classList.add("is-editing");
    row.insertBefore(edit, link.nextSibling);

    input.focus();
    input.select();

    var settled = false;

    function restore(text) {
      if (settled) { return; }
      settled = true;
      titleEl.textContent = text;
      edit.remove();
      link.classList.remove("is-editing");
      row.classList.remove("is-editing");
    }

    function commit() {
      var next = input.value.trim();
      // Nothing typed, or nothing changed: put the old name back rather than
      // sending a request that would either fail or do nothing.
      if (!next || next === original) { return restore(original); }

      restore(next);
      post("/chat/" + row.dataset.conversation + "/rename", { title: next })
        .then(function (data) {
          // The server trims and caps, so the stored name is the truth.
          if (data && data.title) { titleEl.textContent = data.title; }
        })
        .catch(function () {
          titleEl.textContent = original;
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

  function setPinned(row, wanted) {
    post("/chat/" + row.dataset.conversation + "/pin", { pinned: wanted })
      .then(function () {
        row.dataset.pinned = wanted ? "1" : "0";
        row.classList.toggle("pinned", wanted);
        renderPinMarker(row, wanted);
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

  // The marker is a button, because the obvious thing to do with a pin icon is
  // click it to take the pin out.
  function renderPinMarker(row, pinned) {
    var existing = row.querySelector(".nav-chat-pin");
    if (!pinned) {
      if (existing) { existing.remove(); }
      return;
    }
    if (existing) { return; }

    var button = document.createElement("button");
    button.type = "button";
    button.className = "nav-chat-pin";
    button.dataset.action = "unpin";
    button.title = "Unpin";
    button.setAttribute("aria-label", "Unpin conversation");
    var template = document.querySelector('#chat-menu [data-action="pin"] svg');
    if (template) { button.appendChild(template.cloneNode(true)); }

    row.insertBefore(button, row.querySelector(".nav-chat-menu"));
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
    var pin = e.target.closest(".nav-chat-pin");
    if (pin) {
      e.preventDefault();
      setPinned(pin.closest(".nav-chat-row"), false);
      return;
    }

    var button = e.target.closest(".nav-chat-menu");
    if (!button) { return; }
    e.preventDefault();
    var row = button.closest(".nav-chat-row");
    if (openFor === row) { closeMenu(); } else { closeMenu(); openMenu(row, button); }
  });

  // Double-click the name to rename.
  //
  // A link navigates on the FIRST click, so by the time a dblclick handler runs
  // the page is already leaving — which is what made renaming impossible. The
  // only way to have both is to hold the navigation briefly and cancel it if a
  // second click arrives. The delay is the cost of the feature; it is short
  // enough to pass for the browser's own latency, and the menu's Rename has no
  // delay at all for anyone who would rather not wait.
  var DOUBLE_CLICK_GRACE = 200;
  var pendingOpen = null;

  list.addEventListener("click", function (e) {
    var link = e.target.closest(".nav-item-chat");
    if (!link) { return; }

    if (e.detail > 1) {
      // Second click of a double click: the first one is already held.
      e.preventDefault();
      return;
    }

    e.preventDefault();
    var href = link.getAttribute("href");
    clearTimeout(pendingOpen);
    pendingOpen = setTimeout(function () { window.location.href = href; },
                             DOUBLE_CLICK_GRACE);
  });

  list.addEventListener("dblclick", function (e) {
    var link = e.target.closest(".nav-item-chat");
    if (!link) { return; }
    e.preventDefault();
    clearTimeout(pendingOpen);      // cancel the navigation we were holding
    startRename(link.closest(".nav-chat-row"));
  });

  menu.addEventListener("click", function (e) {
    var item = e.target.closest("[data-action]");
    if (!item || !openFor) { return; }
    var row = openFor;
    closeMenu();

    if (item.dataset.action === "pin") { setPinned(row, row.dataset.pinned !== "1"); }
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
