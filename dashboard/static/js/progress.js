/* progress.js — drag paper cards between Kanban columns to change reading status. */

(function () {
  "use strict";

  const board = document.querySelector(".kanban");
  if (!board) { return; }

  const statusUrlTemplate = window.PROGRESS_STATUS_URL || "/paper/PID/status";
  let dragCard = null;

  board.addEventListener("dragstart", function (e) {
    dragCard = e.target.closest(".kanban-card");
    if (dragCard) { dragCard.classList.add("dragging"); }
  });

  board.addEventListener("dragend", function () {
    if (dragCard) { dragCard.classList.remove("dragging"); }
    board.querySelectorAll(".drop-target").forEach(function (n) { n.classList.remove("drop-target"); });
    dragCard = null;
  });

  board.querySelectorAll(".kanban-col").forEach(function (col) {
    col.addEventListener("dragover", function (e) { e.preventDefault(); col.classList.add("drop-target"); });
    col.addEventListener("dragleave", function () { col.classList.remove("drop-target"); });

    col.addEventListener("drop", async function (e) {
      e.preventDefault();
      col.classList.remove("drop-target");
      if (!dragCard) { return; }

      const fromCol = dragCard.closest(".kanban-col");
      const newStatus = col.dataset.status;
      if (fromCol === col) { return; }

      col.querySelector(".kanban-list").appendChild(dragCard);
      recount(fromCol);
      recount(col);

      const url = statusUrlTemplate.replace("PID", dragCard.dataset.paperId);
      try {
        await window.RC.postJSON(url, { status: newStatus });
      } catch (err) {
        window.RC.toast(err.message + " — reload to resync.", "error");
      }
    });
  });

  function recount(col) {
    const n = col.querySelectorAll(".kanban-card").length;
    col.querySelector(".kanban-count").textContent = n;
  }
})();
