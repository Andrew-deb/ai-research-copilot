/* Finite internal reference links; user text is always rendered as text nodes. */
(function () {
  "use strict";
  var uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
  var pages = ["dashboard", "search", "collections", "progress", "notes", "goals"];
  function href(item) {
    if (item.available === false) { return null; }
    if (item.kind === "page") { return pages.indexOf(item.id) >= 0 ? "/" + item.id : null; }
    if (!(new RegExp("^" + uuid + "$")).test(item.id)) { return null; }
    return item.kind === "paper" ? "/paper/" + item.id : item.kind === "collection" ? "/collection/" + item.id :
      item.kind === "note" ? "/notes#note-" + item.id : item.kind === "goal" ? "/goals#goal-" + item.id : null;
  }
  function token(item) {
    var destination = href(item);
    if (!destination) { return ""; }
    var label = String(item.label || item.kind).replace(/[\[\]\\\r\n]/g, " ").trim().slice(0, 100);
    return "[" + (item.kind === "page" ? "▤ " : "@") + label + "](" + destination + ")";
  }
  function format(text, items) {
    var tokens = items.map(token).filter(function (value) { return value && text.indexOf(value) < 0; });
    return text + (tokens.length ? "\n\n" + tokens.join(" ") : "");
  }
  function render(container, text) {
    var pattern = /\[([@▤][^\]\n]{1,102})\]\((\/[^\s)]+)\)/g;
    var match, cursor = 0;
    var safe = new RegExp("^/(?:paper/" + uuid + "|collection/" + uuid + "|notes#note-" + uuid + "|goals#goal-" + uuid + "|" + pages.join("|") + ")$");
    while ((match = pattern.exec(text))) {
      if (!safe.test(match[2])) { continue; }
      container.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      var link = document.createElement("a"); link.href = match[2]; link.textContent = match[1];
      link.className = "wick-prompt-reference"; link.target = "_blank"; link.rel = "noopener";
      container.appendChild(link); cursor = pattern.lastIndex;
    }
    container.appendChild(document.createTextNode(text.slice(cursor)));
  }
  window.WickMentions = { href: href, token: token, format: format, render: render };
})();
