/* rails.js — dragging the navigation and the notes panel wider or narrower.

   How much of the window a reference list or a notepad deserves is not
   something a stylesheet can know: it depends on what is being read, how long
   the notes are and how wide the screen is. Both rails are driven by a CSS
   custom property, so one number moved here moves the rail, the margin the
   page carries and everything positioned against it, with no layout code.

   Pointer events rather than mouse events, so a stylus and a trackpad behave
   the same, and pointer capture so a fast drag that leaves the handle does not
   drop the gesture halfway. */

(function () {
  "use strict";

  var RAILS = [
    {
      handle: "sidebar-resize",
      prop: "--sidebar-w",
      store: "rc-rail-sidebar",
      // Grows rightwards: the pointer's x IS the width.
      widthFrom: function (x) { return x; },
      min: 200,
      max: 420,
    },
    {
      handle: "notes-resize",
      prop: "--notes-w",
      store: "rc-rail-notes",
      // Grows leftwards from the right edge, so the width is what is left.
      widthFrom: function (x) { return window.innerWidth - x; },
      min: 300,
      max: 760,
    },
  ];

  function clamp(rail, width) {
    // Never wider than half the window, whatever the configured max: two rails
    // that between them leave no page are two rails nobody can use.
    var ceiling = Math.min(rail.max, Math.round(window.innerWidth * 0.5));
    return Math.max(rail.min, Math.min(ceiling, Math.round(width)));
  }

  function apply(rail, width) {
    document.documentElement.style.setProperty(rail.prop, clamp(rail, width) + "px");
  }

  function remember(rail, width) {
    try { window.localStorage.setItem(rail.store, String(clamp(rail, width))); }
    catch (e) { /* private mode: the width simply does not persist */ }
  }

  function restore(rail) {
    var saved;
    try { saved = window.localStorage.getItem(rail.store); } catch (e) { return; }
    var width = parseInt(saved, 10);
    if (width > 0) { apply(rail, width); }
  }

  function current(rail) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(rail.prop);
    return parseInt(value, 10) || rail.min;
  }

  RAILS.forEach(function (rail) {
    var handle = document.getElementById(rail.handle);
    if (!handle) { return; }          // signed out: no notes panel to resize

    restore(rail);
    handle.setAttribute("aria-valuemin", String(rail.min));
    handle.setAttribute("aria-valuemax", String(rail.max));
    handle.setAttribute("aria-valuenow", String(current(rail)));

    /* ------------------------------------------------------------- pointer */

    handle.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      handle.classList.add("is-dragging");
      document.body.classList.add("rail-resizing");
    });

    handle.addEventListener("pointermove", function (e) {
      if (!handle.hasPointerCapture(e.pointerId)) { return; }
      apply(rail, rail.widthFrom(e.clientX));
    });

    function end(e) {
      if (!handle.hasPointerCapture(e.pointerId)) { return; }
      handle.releasePointerCapture(e.pointerId);
      handle.classList.remove("is-dragging");
      document.body.classList.remove("rail-resizing");

      var width = current(rail);
      handle.setAttribute("aria-valuenow", String(width));
      remember(rail, width);
    }

    handle.addEventListener("pointerup", end);
    // Cancel fires when the browser takes the gesture over (a system swipe, a
    // dropped stylus). Without this the page would stay in resizing mode with
    // selection disabled and no way back.
    handle.addEventListener("pointercancel", end);

    /* ------------------------------------------------------------ keyboard */

    handle.addEventListener("keydown", function (e) {
      // A separator that only a mouse can move is a separator half the people
      // using it cannot reach. Shift for a coarse step, Home/End for the ends.
      var step = e.shiftKey ? 48 : 12;
      var width = current(rail);
      var next = null;

      if (e.key === "ArrowLeft") { next = width + (rail.prop === "--notes-w" ? step : -step); }
      else if (e.key === "ArrowRight") { next = width + (rail.prop === "--notes-w" ? -step : step); }
      else if (e.key === "Home") { next = rail.min; }
      else if (e.key === "End") { next = rail.max; }
      if (next === null) { return; }

      e.preventDefault();
      apply(rail, next);
      var settled = current(rail);
      handle.setAttribute("aria-valuenow", String(settled));
      remember(rail, settled);
    });

    // Double-click resets, the usual escape hatch for a rail dragged somewhere
    // unhelpful.
    handle.addEventListener("dblclick", function () {
      document.documentElement.style.removeProperty(rail.prop);
      try { window.localStorage.removeItem(rail.store); } catch (err) { /* ignore */ }
      handle.setAttribute("aria-valuenow", String(current(rail)));
    });
  });

  // A window narrowed past what a saved width allows would otherwise leave a
  // rail taking most of it.
  window.addEventListener("resize", function () {
    RAILS.forEach(function (rail) {
      if (document.getElementById(rail.handle)) { apply(rail, current(rail)); }
    });
  });
})();
