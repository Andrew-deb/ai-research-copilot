/* paper_detail.js — reading-status buttons, add-to-collection, inline note save. */

(function () {
  "use strict";

  const root = document.querySelector(".paper-detail");
  if (!root) { return; }
  const paperId = root.dataset.paperId;

  // ---------- Reading status ----------
  root.querySelectorAll(".status-btn").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      try {
        const data = await window.RC.postJSON(btn.dataset.action, { status: btn.dataset.status });
        root.querySelectorAll(".status-btn").forEach(function (b) { b.classList.remove("is-current"); });
        btn.classList.add("is-current");
        window.RC.toast("Marked as " + data.progress.status.replace("_", " ") + ".");
      } catch (err) {
        window.RC.toast(err.message, "error");
      }
    });
  });

  // ---------- Add to collection ----------
  const addForm = document.getElementById("add-to-collection");
  if (addForm) {
    addForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const collectionId = addForm.collection_id.value;
      if (!collectionId) { return; }
      const url = addForm.dataset.urlTemplate.replace("CID", collectionId);
      try {
        await window.RC.postJSON(url, { paper_id: paperId });
        window.RC.toast("Added to collection.");
        addForm.reset();
      } catch (err) {
        window.RC.toast(err.message, "error");
      }
    });
  }

  // ---------- Inline note save ----------
  const noteForm = document.getElementById("note-form");
  if (noteForm) {
    noteForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const text = noteForm.note_text.value.trim();
      if (!text) { return; }
      try {
        const data = await window.RC.postJSON(noteForm.action, { note_text: text });
        const li = document.createElement("li");
        li.innerHTML = "<p></p><time>just now</time>";
        li.querySelector("p").textContent = data.note.note_text;
        document.querySelector(".note-list").prepend(li);
        noteForm.reset();
        window.RC.toast("Note saved.");
      } catch (err) {
        window.RC.toast(err.message, "error");
      }
    });
  }
})();
