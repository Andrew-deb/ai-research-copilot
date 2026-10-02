/* collections.js — drag-to-reorder papers in a collection's reading list. */

(function () {
  "use strict";

  const list = document.querySelector(".js-sortable");
  const root = document.querySelector(".collection-detail");
  if (!root) { return; }

  if (list) {

  const reorderUrl = root.dataset.reorderUrl;
  let dragEl = null;

  list.addEventListener("dragstart", function (e) {
    dragEl = e.target.closest(".reading-item");
    if (dragEl) { dragEl.classList.add("dragging"); }
  });

  list.addEventListener("dragend", function () {
    if (dragEl) { dragEl.classList.remove("dragging"); }
    list.querySelectorAll(".drop-target").forEach(function (n) { n.classList.remove("drop-target"); });
    dragEl = null;
  });

  list.addEventListener("dragover", function (e) {
    e.preventDefault();
    const over = e.target.closest(".reading-item");
    if (!over || over === dragEl) { return; }
    const rect = over.getBoundingClientRect();
    const after = (e.clientY - rect.top) / rect.height > 0.5;
    list.insertBefore(dragEl, after ? over.nextSibling : over);
  });

  list.addEventListener("drop", async function (e) {
    e.preventDefault();
    const ids = Array.from(list.querySelectorAll(".reading-item")).map(function (li) {
      return li.dataset.paperId;
    });
    // Optimistically renumber the sequence badges.
    list.querySelectorAll(".reading-item .seq").forEach(function (badge, i) {
      badge.textContent = i + 1;
    });
    try {
      await window.RC.postJSON(reorderUrl, { ordered_paper_ids: ids });
      window.RC.toast("Reading order saved.");
    } catch (err) {
      window.RC.toast(err.message + " — reload to see the saved order.", "error");
    }
  });
  }
})();

/* Native dialog keeps focus inside the picker and restores it on close. */
(function () {
  "use strict";
  const dialog = document.getElementById("paper-picker");
  if (!dialog) return;
  const form = document.getElementById("picker-search");
  const results = document.getElementById("picker-results");
  const status = document.getElementById("picker-status");
  const add = document.getElementById("picker-add");
  const more = document.getElementById("picker-more");
  const selected = new Map();
  let controller, nextCursor = null, busy = false, changed = false, generation = 0;
  let activeQuery = "", activeScope = "corpus";
  function update() {
    document.getElementById("picker-selected").textContent = selected.size + " selected";
    add.disabled = busy || selected.size === 0;
  }
  function render(paper) {
    const row = document.createElement("label"); row.className = "picker-paper";
    const check = document.createElement("input"); check.type = "checkbox";
    check.value = paper.paper_id; check.disabled = paper.already_added;
    if (paper.already_added) check.dataset.added = "true";
    check.checked = selected.has(paper.paper_id);
    check.addEventListener("change", function () {
      if (check.checked && selected.size >= 20) {
        check.checked = false; status.textContent = "Add up to 20 papers at a time."; return;
      }
      if (check.checked) selected.set(paper.paper_id, paper); else selected.delete(paper.paper_id);
      update();
    });
    const body = document.createElement("div");
    const title = document.createElement("a"); title.href = paper.url; title.target = "_blank";
    title.rel = "noopener"; title.textContent = paper.title;
    const meta = document.createElement("p"); meta.className = "paper-meta";
    meta.textContent = [paper.authors, paper.publication_year, paper.venue].filter(Boolean).join(" · ");
    const abstract = document.createElement("p"); abstract.className = "picker-abstract";
    abstract.textContent = paper.abstract || "No abstract available.";
    body.append(title, meta, abstract);
    if (paper.already_added) { const badge = document.createElement("span"); badge.textContent = "Already in collection"; body.append(badge); }
    row.append(check, body); results.append(row);
  }
  async function load(append) {
    if (busy) return;
    if (controller) controller.abort();
    controller = new AbortController();
    const requestGeneration = ++generation;
    if (!append) {
      activeQuery = form.elements.q.value.trim(); activeScope = form.elements.scope.value;
      results.replaceChildren(); nextCursor = null;
    }
    const params = new URLSearchParams({ q: activeQuery, scope: activeScope });
    if (append && nextCursor) params.set("cursor", nextCursor);
    status.textContent = "Finding papers…"; more.hidden = true; add.disabled = true;
    try {
      const response = await fetch(dialog.dataset.searchUrl + "?" + params, { signal: controller.signal, headers: { Accept: "application/json" } });
      const data = await response.json();
      if (requestGeneration !== generation) return;
      if (!response.ok) throw new Error(data.detail || data.error || data.message || "Could not load papers.");
      data.items.forEach(render); nextCursor = data.next_cursor;
      more.hidden = !nextCursor;
      status.textContent = results.children.length ? "Select papers to add. Selection stays while you search." : "No matching papers in this scope. Try a broader topic, title, or author.";
    } catch (error) { if (requestGeneration === generation && error.name !== "AbortError") status.textContent = error.message; }
    finally { if (requestGeneration === generation) update(); }
  }
  document.querySelectorAll(".js-add-papers").forEach(function (button) {
    button.addEventListener("click", function () { dialog.showModal(); load(false); });
  });
  form.addEventListener("submit", function (event) { event.preventDefault(); load(false); });
  more.addEventListener("click", function () { load(true); });
  document.getElementById("picker-close").addEventListener("click", function () { if (!busy) dialog.close(); });
  dialog.addEventListener("cancel", function (event) { if (busy) event.preventDefault(); });
  dialog.addEventListener("close", function () { if (controller) controller.abort(); if (changed) window.location.reload(); });
  add.addEventListener("click", async function () {
    busy = true; ++generation; if (controller) controller.abort(); update(); form.querySelectorAll("input, select, button").forEach(el => el.disabled = true);
    results.querySelectorAll("input").forEach(el => el.disabled = true); more.disabled = true;
    let count = 0;
    try {
      for (const [id, paper] of selected) {
        status.textContent = "Adding " + paper.title + "…";
        await window.RC.postJSON(dialog.dataset.addUrl, { paper_id: id });
        selected.delete(id); changed = true; count += 1;
        results.querySelectorAll("input").forEach(function (check) {
          if (check.value === id) { check.checked = false; check.dataset.added = "true"; }
        });
      }
      window.RC.toast(count + " paper" + (count === 1 ? "" : "s") + " added.");
      dialog.close();
    } catch (error) {
      status.textContent = count + " added. Remaining selections were kept. " + error.message;
    } finally {
      busy = false; form.querySelectorAll("input, select, button").forEach(el => el.disabled = false);
      more.disabled = false;
      results.querySelectorAll("input").forEach(el => el.disabled = el.dataset.added === "true");
      update();
    }
  });
})();
