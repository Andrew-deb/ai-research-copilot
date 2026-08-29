/* collections.js — drag-to-reorder papers in a collection's reading list. */

(function () {
  "use strict";

  const list = document.querySelector(".js-sortable");
  const root = document.querySelector(".collection-detail");
  if (!list || !root) { return; }

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
})();
