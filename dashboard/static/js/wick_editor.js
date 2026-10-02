/* Inline references keep the existing textarea contract for drafts and chat runs. */
(function () {
  "use strict";
  var input = document.getElementById("chat-input"), controls = document.getElementById("wick-context-controls");
  if (!input || !controls) { return; }
  var editor = document.createElement("div"), mode = document.getElementById("chat-mode");
  editor.id = "wick-prompt-editor"; editor.className = "composer-input wick-prompt-editor";
  editor.setAttribute("role", "textbox"); editor.setAttribute("aria-multiline", "true");
  editor.setAttribute("aria-label", "Ask Wick"); editor.dataset.placeholder = "Type @ to include an asset and # for pages";
  input.after(editor);
  var nativeValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
  var savedRange = null, contextItems = null, composing = false;
  function serialize(node, plain) {
    if (node.nodeType === 3) { return node.textContent; }
    if (node.dataset && node.dataset.referenceKind) {
      return plain ? "" : window.WickMentions.token(itemFromNode(node));
    }
    if (node.nodeName === "BR") { return "\n"; }
    var text = "", previousBlock = false;
    Array.from(node.childNodes).forEach(function (child, index) {
      var block = /^(DIV|P)$/.test(child.nodeName), value = serialize(child, plain);
      if (index && (block || previousBlock) && !text.endsWith("\n")) { text += "\n"; }
      text += block && value === "\n" ? "" : value; previousBlock = block;
    });
    return text;
  }
  function itemFromNode(node) {
    return {kind: node.dataset.referenceKind, id: node.dataset.referenceId, label: node.dataset.referenceLabel,
      available: node.dataset.unavailable !== "true"};
  }
  function atoms() { return Array.from(editor.querySelectorAll("[data-reference-kind]")); }
  function references() { return atoms().map(itemFromNode); }
  function writeValue() { nativeValue.set.call(input, serialize(editor)); editor.dataset.empty = String(!serialize(editor, true).trim()); }
  function notify() { writeValue(); input.dispatchEvent(new Event("input", { bubbles: true })); }
  function referenceNode(item) {
    var link = document.createElement("a"), destination = window.WickMentions.href(item);
    link.className = "wick-inline-reference"; link.setAttribute("contenteditable", "false");
    link.dataset.referenceKind = item.kind; link.dataset.referenceId = item.id; link.dataset.referenceLabel = item.label;
    link.dataset.unavailable = String(item.available === false);
    link.textContent = (item.kind === "page" ? "#" : "@") + item.label + (item.available === false ? " (unavailable)" : "");
    if (destination) { link.href = destination; link.target = "_blank"; link.rel = "noopener"; }
    link.title = item.available === false ? "Unavailable context; delete to remove" : "Open " + item.label;
    return link;
  }
  function remember() {
    var selection = window.getSelection();
    if (selection.rangeCount && (document.activeElement === editor || editor.contains(document.activeElement)) && editor.contains(selection.anchorNode) && editor.contains(selection.focusNode)) {
      savedRange = selection.getRangeAt(0).cloneRange();
    }
  }
  function insert(item, replacement) {
    var range = replacement || savedRange;
    if (!range || !editor.contains(range.commonAncestorContainer)) { range = document.createRange(); range.selectNodeContents(editor); range.collapse(false); }
    var link = referenceNode(item), space = document.createTextNode(" ");
    range.deleteContents(); range.insertNode(space); range.insertNode(link);
    range.setStartAfter(space); range.collapse(true); savedRange = range.cloneRange();
    focusAt(range); notify();
  }
  function sync(items) {
    contextItems = items.slice();
    atoms().forEach(function (node) {
      var item = items.find(function (value) { return value.kind === node.dataset.referenceKind && value.id === node.dataset.referenceId; });
      if (!item) { node.remove(); }
      else {
        var updated = referenceNode(item); node.textContent = updated.textContent;
        node.dataset.referenceLabel = updated.dataset.referenceLabel; node.dataset.unavailable = updated.dataset.unavailable;
        if (updated.hasAttribute("href")) { node.setAttribute("href", updated.getAttribute("href")); } else { node.removeAttribute("href"); }
        node.title = updated.title;
      }
    });
    writeValue();
  }
  function setText(text) {
    editor.replaceChildren(); window.WickMentions.render(editor, text);
    editor.querySelectorAll("a").forEach(function (node) {
      var item = window.WickMentions.fromHref(node.getAttribute("href"), node.textContent.slice(1).trim());
      if (item) { node.replaceWith(referenceNode(item)); }
    });
    if (contextItems) { sync(contextItems); } else { writeValue(); }
    savedRange = document.createRange(); savedRange.selectNodeContents(editor); savedRange.collapse(false);
    document.dispatchEvent(new Event("wick:editor-reset"));
  }
  function shortcut() {
    var selection = window.getSelection();
    if (!selection.rangeCount || !selection.isCollapsed || !editor.contains(selection.anchorNode) || selection.anchorNode.nodeType !== 3) { return null; }
    var node = selection.anchorNode;
    if (node.parentElement.closest("[data-reference-kind]")) { return null; }
    var prefix = node.textContent.slice(0, selection.anchorOffset), match = /(?:^|\s)([@#])([^\s@#]*)$/.exec(prefix);
    if (!match) { return null; }
    var range = document.createRange(); range.setStart(node, prefix.length - match[2].length - 1); range.setEnd(node, prefix.length);
    return { category: match[1] === "@" ? "assets" : "pages", query: match[2], range: range };
  }
  function enabled() { return !mode || mode.value === "wick"; }
  function updateMode() {
    editor.hidden = !enabled(); input.hidden = enabled();
    editor.setAttribute("contenteditable", String(!input.disabled)); editor.setAttribute("aria-disabled", String(input.disabled));
  }
  function focusAt(range) {
    editor.focus({ preventScroll: true });
    if (!range || !editor.contains(range.commonAncestorContainer)) { range = document.createRange(); range.selectNodeContents(editor); range.collapse(false); }
    var selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    savedRange = range.cloneRange();
  }
  var originalFocus = input.focus.bind(input), originalSelection = input.setSelectionRange.bind(input);
  input.focus = function () { if (enabled()) { focusAt(savedRange); } else { originalFocus(); } };
  input.setSelectionRange = function (start, end) {
    if (!enabled()) { originalSelection(start, end); return; }
    var range = document.createRange(); range.selectNodeContents(editor); range.collapse(false);
    var selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); remember();
  };
  var initialText = nativeValue.get.call(input);
  Object.defineProperty(input, "value", { get: function () { return nativeValue.get.call(input); }, set: function (text) {
    nativeValue.set.call(input, text); if (enabled()) { setText(String(text)); }
  } });
  editor.addEventListener("input", function () { remember(); notify(); });
  editor.addEventListener("compositionstart", function () { composing = true; });
  editor.addEventListener("compositionend", function () { composing = false; notify(); });
  editor.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !composing && !event.isComposing && !event.defaultPrevented) {
      event.preventDefault(); document.getElementById("chat-composer").requestSubmit();
    }
  });
  editor.addEventListener("paste", function (event) {
    event.preventDefault(); var text = event.clipboardData.getData("text/plain"); remember();
    var range = savedRange || document.createRange();
    if (!savedRange) { range.selectNodeContents(editor); range.collapse(false); }
    range.deleteContents(); var node = document.createTextNode(text); range.insertNode(node);
    range.setStartAfter(node); range.collapse(true); var selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    remember(); notify();
  });
  editor.addEventListener("drop", function (event) { event.preventDefault(); });
  document.addEventListener("selectionchange", remember);
  new MutationObserver(updateMode).observe(input, { attributes: true, attributeFilter: ["disabled"] });
  if (mode) { mode.addEventListener("change", function () { if (enabled()) { setText(nativeValue.get.call(input)); } else { nativeValue.set.call(input, serialize(editor, true)); } updateMode(); }); }
  setText(initialText); updateMode();
  window.WickEditor = { element: editor, references: references, sync: sync, insert: insert, remember: remember, shortcut: shortcut, composing: function () { return composing; } };
})();
