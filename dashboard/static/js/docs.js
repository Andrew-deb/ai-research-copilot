/* docs.js — behaviour for /docs and /help.

   One file, two independent blocks, each of which exits immediately if its page
   is not the one loaded. Both search boxes do real work: a search input that
   only looks like one is worse than no input at all, because someone who types a
   question and gets no response concludes the answer does not exist rather than
   that the control is decorative.

   Everything here is an enhancement. With JavaScript off, both pages are still
   complete documents: the anchors navigate, and the FAQ opens, because it is
   built from <details> rather than scripted panels. */

(function () {
  "use strict";

  /* ---------------------------------------------------------------- helpers */

  function terms(value) {
    var q = value.trim().toLowerCase();
    return q ? q.split(/\s+/) : [];
  }

  // Every word must appear, so a second word narrows the result rather than
  // widening it the way an any-term match would.
  function matches(text, words) {
    for (var i = 0; i < words.length; i++) {
      if (text.indexOf(words[i]) === -1) { return false; }
    }
    return true;
  }

  function slug(text) {
    return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  /* ------------------------------------------------------------ /docs page */

  (function docsPage() {
    var article = document.getElementById("doc-article");
    var nav = document.getElementById("doc-nav");
    if (!article || !nav) { return; }

    var filter = document.getElementById("doc-filter");
    var none = document.getElementById("doc-none");
    var tocList = document.getElementById("doc-toc-list");

    var sections = Array.prototype.slice.call(article.querySelectorAll(".doc-section"));
    var navItems = Array.prototype.slice.call(nav.querySelectorAll(".doc-nav-item"));
    var groups = Array.prototype.slice.call(nav.querySelectorAll(".doc-nav-group"));

    // Subheadings need ids before anything can link to them, and writing them
    // into the template by hand is how one of them ends up not matching.
    sections.forEach(function (section) {
      section.__text = section.textContent.toLowerCase();
      Array.prototype.forEach.call(section.querySelectorAll("h3"), function (h3) {
        if (!h3.id) { h3.id = section.id + "-" + slug(h3.textContent); }
      });
    });

    function navItemFor(id) {
      for (var i = 0; i < navItems.length; i++) {
        if (navItems[i].getAttribute("href") === "#" + id) { return navItems[i]; }
      }
      return null;
    }

    /* ---- filter ---- */

    function applyFilter() {
      var words = terms(filter ? filter.value : "");
      var shown = 0;

      sections.forEach(function (section) {
        var hit = matches(section.__text, words);
        section.hidden = !hit;
        var item = navItemFor(section.id);
        if (item) { item.hidden = !hit; }
        if (hit) { shown += 1; }
      });

      // A group heading with nothing under it is noise, not structure.
      groups.forEach(function (group) {
        var items = group.querySelectorAll(".doc-nav-item");
        var any = false;
        Array.prototype.forEach.call(items, function (i) { if (!i.hidden) { any = true; } });
        group.hidden = !any;
      });

      if (none) { none.hidden = shown !== 0; }
      spy();
    }

    /* ---- position ---- */

    // The rail shows the subheadings of the section you are in, not every
    // heading on the page: the lefthand nav already lists the sections, and
    // repeating them on the right would say the same thing twice.
    var activeId = null;

    function renderFor(section) {
      var item = navItemFor(section.id);

      navItems.forEach(function (i) { i.classList.remove("is-active"); });
      var stale = nav.querySelector(".doc-nav-sub");
      if (stale) { stale.parentNode.removeChild(stale); }

      var heads = Array.prototype.slice.call(section.querySelectorAll("h3"));

      if (item) {
        item.classList.add("is-active");
        if (heads.length) {
          var sub = document.createElement("ul");
          sub.className = "doc-nav-sub";
          heads.forEach(function (h3) {
            var li = document.createElement("li");
            var a = document.createElement("a");
            a.href = "#" + h3.id;
            a.textContent = h3.textContent;
            li.appendChild(a);
            sub.appendChild(li);
          });
          item.parentNode.insertBefore(sub, item.nextSibling);
        }
      }

      if (tocList) {
        tocList.innerHTML = "";
        heads.forEach(function (h3) {
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.href = "#" + h3.id;
          a.textContent = h3.textContent;
          li.appendChild(a);
          tocList.appendChild(li);
        });
      }
    }

    function markToc(section) {
      if (!tocList) { return; }
      var heads = Array.prototype.slice.call(section.querySelectorAll("h3"));
      var current = null;
      heads.forEach(function (h3) {
        if (h3.getBoundingClientRect().top <= 140) { current = h3.id; }
      });
      Array.prototype.forEach.call(tocList.querySelectorAll("a"), function (a) {
        a.classList.toggle("is-active", a.getAttribute("href") === "#" + current);
      });
    }

    function spy() {
      var visible = sections.filter(function (s) { return !s.hidden; });
      if (!visible.length) { return; }

      var current = visible[0];
      visible.forEach(function (s) {
        if (s.getBoundingClientRect().top <= 120) { current = s; }
      });

      if (current.id !== activeId) {
        activeId = current.id;
        renderFor(current);
      }
      markToc(current);
    }

    var ticking = false;
    window.addEventListener("scroll", function () {
      if (ticking) { return; }
      ticking = true;
      window.requestAnimationFrame(function () { ticking = false; spy(); });
    }, { passive: true });

    if (filter) {
      filter.addEventListener("input", applyFilter);
      // Escape clears rather than closing: there is nothing to close, and the
      // whole document back is the state someone actually wants.
      filter.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { filter.value = ""; applyFilter(); }
      });
    }

    spy();
  })();

  /* ------------------------------------------------------------ /help page */

  (function helpPage() {
    var faq = document.getElementById("help-faq");
    var filter = document.getElementById("help-filter");
    if (!faq || !filter) { return; }

    var none = document.getElementById("help-none");
    var cards = document.getElementById("help-cards");
    var items = Array.prototype.slice.call(faq.querySelectorAll(".faq-item"));
    var heads = Array.prototype.slice.call(faq.querySelectorAll(".help-faq-head"));

    items.forEach(function (item) { item.__text = item.textContent.toLowerCase(); });

    function apply() {
      var words = terms(filter.value);
      var searching = words.length > 0;
      var shown = 0;

      items.forEach(function (item) {
        var hit = matches(item.__text, words);
        item.hidden = !hit;
        // Open what matched: the answer is the point, and leaving someone to
        // click each result to find out is a second search.
        if (searching) { item.open = hit; }
        if (hit) { shown += 1; }
      });

      // A heading is shown only if something still sits under it. Walking
      // forward from each heading is what keeps the grouping honest as items
      // disappear.
      heads.forEach(function (head) {
        var any = false;
        var node = head.nextElementSibling;
        while (node && !node.classList.contains("help-faq-head")) {
          if (node.classList.contains("faq-item") && !node.hidden) { any = true; }
          node = node.nextElementSibling;
        }
        head.hidden = !any;
      });

      // The cards are a browsing aid. Once someone is searching they are in the
      // way, and they scroll the answers below the fold for no benefit.
      if (cards) { cards.hidden = searching; }
      if (none) { none.hidden = shown !== 0; }
    }

    filter.addEventListener("input", apply);
    filter.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { filter.value = ""; apply(); }
    });

    // Chips are shortcuts into the same filter, not a second mechanism.
    Array.prototype.forEach.call(document.querySelectorAll(".help-chip"), function (chip) {
      chip.addEventListener("click", function () {
        filter.value = chip.getAttribute("data-query") || chip.textContent.trim();
        apply();
        filter.focus();
      });
    });
  })();
})();
