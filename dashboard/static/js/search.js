/*
 * search.js — one box, three modes.
 *
 * Keyword and Semantic are ordinary GET submissions: the server renders a list
 * of papers and the page reloads. Ask is different in kind — it spends a model
 * call and answers in prose with citations — so it is intercepted here and
 * posted to /search/ask, leaving the results already on the page alone.
 *
 * Ask used to be a second panel further down, below the suggestion chips, which
 * on a phone meant it was reliably never seen. Folding it into the mode switch
 * is what this file mostly exists to do now.
 */
(function () {
  "use strict";

  const form = document.querySelector("[data-search-form]");
  if (!form) { return; }

  const input = form.querySelector("[data-search-input]");
  const submit = form.querySelector("[data-search-submit]");
  const hint = document.querySelector("[data-search-hint]");
  const answerBox = document.getElementById("rag-answer");

  const MODES = {
    keyword: {
      label: "Search",
      placeholder: "transformer attention, retrieval-augmented generation, …",
      hint: "Matches the words you type against titles and abstracts."
    },
    semantic: {
      label: "Search",
      placeholder: "papers about making attention cheaper",
      hint: "Matches meaning rather than words, so related work surfaces even when it uses different terms."
    },
    ask: {
      label: "Ask",
      placeholder: "What are the main approaches to reducing attention's quadratic cost?",
      hint: "Reads the most relevant papers and writes an answer that cites them. Uses one of your daily questions."
    }
  };

  function currentMode() {
    const checked = form.querySelector("input[name=mode]:checked");
    return (checked && checked.value) || "keyword";
  }

  function applyMode() {
    const mode = MODES[currentMode()] || MODES.keyword;
    submit.textContent = mode.label;
    input.placeholder = mode.placeholder;
    if (hint) { hint.textContent = mode.hint; }
  }

  form.querySelectorAll("input[name=mode]").forEach(function (radio) {
    radio.addEventListener("change", function () {
      applyMode();
      // Switching mode does not re-run the previous question: the answer on
      // screen was produced by Ask and would be untrue as a label for a
      // keyword search.
      if (answerBox && currentMode() !== "ask") { answerBox.hidden = true; }
      input.focus();
    });
  });

  applyMode();

  /* ------------------------------------------------------------------ ask -- */

  form.addEventListener("submit", async function (event) {
    if (currentMode() !== "ask" || !answerBox) { return; }   // let the GET through

    event.preventDefault();
    const question = input.value.trim();
    if (!question) { return; }

    const body = answerBox.querySelector(".rag-answer-body");
    const sources = answerBox.querySelector(".rag-sources");

    submit.disabled = true;
    submit.textContent = "Thinking…";
    answerBox.hidden = false;
    body.textContent = "Retrieving papers and synthesising an answer…";
    sources.innerHTML = "";

    try {
      const data = await window.RC.postJSON("/search/ask", { question: question });
      body.textContent = data.answer || data.message || "No answer produced.";
      sources.innerHTML = (data.sources || []).map(function (s) {
        const pct = s.similarity != null ? " — " + Math.round(s.similarity * 100) + "% match" : "";
        return "<li><a href='/paper/" + s.paper_id + "'>[" + s.number + "] " +
          window.RC.escapeHtml(s.title) + "</a>" +
          (s.publication_year ? " (" + s.publication_year + ")" : "") + pct + "</li>";
      }).join("");
    } catch (err) {
      body.textContent = "";
      window.RC.toast(err.message, "error");
      answerBox.hidden = true;
    } finally {
      submit.disabled = false;
      applyMode();
    }
  });
})();
