/* search.js — RAG "ask a question" box on the search page. */

(function () {
  "use strict";

  const form = document.getElementById("rag-form");
  if (!form) { return; }

  const answerBox = document.getElementById("rag-answer");
  const body = answerBox.querySelector(".rag-answer-body");
  const sources = answerBox.querySelector(".rag-sources");
  const submitBtn = form.querySelector("button[type=submit]");

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    const question = form.question.value.trim();
    if (!question) { return; }

    submitBtn.disabled = true;
    submitBtn.textContent = "Thinking…";
    answerBox.hidden = false;
    body.textContent = "Retrieving papers and synthesising an answer…";
    sources.innerHTML = "";

    try {
      const data = await window.RC.postJSON("/search/ask", { question: question });
      body.textContent = data.answer || data.message || "No answer produced.";
      sources.innerHTML = (data.sources || []).map(function (s) {
        const pct = s.similarity != null ? " — " + Math.round(s.similarity * 100) + "% match" : "";
        return "<li><a href='/paper/" + s.paper_id + "'>[" + s.number + "] " +
          window.RC.escapeHtml(s.title) + "</a>" + (s.publication_year ? " (" + s.publication_year + ")" : "") + pct + "</li>";
      }).join("");
    } catch (err) {
      body.textContent = "";
      window.RC.toast(err.message, "error");
      answerBox.hidden = true;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Ask";
    }
  });
})();
