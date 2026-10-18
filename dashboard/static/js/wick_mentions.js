/* Finite internal reference links; user text is always rendered as text nodes. */
(function () {
  "use strict";
  var uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
  var pages = ["dashboard", "search", "collections", "progress", "notes", "goals"];
  function href(item) {
    if (item.available === false) { return null; }
    if (item.kind === "page") { return pages.indexOf(item.id) >= 0 ? "/" + item.id : null; }
    if (!(new RegExp("^" + uuid + "$")).test(item.id)) { return null; }
    if (item.kind === "attachment") { return "/uploads/" + item.id + "/download"; }
    return item.kind === "paper" ? "/paper/" + item.id : item.kind === "collection" ? "/collection/" + item.id :
      item.kind === "note" ? "/notes#note-" + item.id : item.kind === "goal" ? "/goals#goal-" + item.id : null;
  }
  function fromHref(destination, label) {
    var item;
    if (/^\/uploads\//.test(destination)) { item = {kind:"attachment", id:destination.split("/")[2], label:label}; }
    else if (/^\/(paper|collection)\//.test(destination)) { var parts = destination.split("/"); item = {kind: parts[1], id: parts[2], label: label}; }
    else if (/^\/(notes#note-|goals#goal-)/.test(destination)) { item = {kind: destination.indexOf("/notes") === 0 ? "note" : "goal", id: destination.split("-").slice(1).join("-"), label: label}; }
    else { item = {kind: "page", id: destination.slice(1), label: label}; }
    return href(item) === destination ? item : null;
  }
  function token(item) {
    var destination = href(item);
    if (!destination) { return ""; }
    var label = String(item.label || item.kind).replace(/[\[\]\\\r\n]/g, " ").trim().slice(0, 100);
    return "[" + (item.kind === "page" ? "#" : item.kind === "attachment" ? "▤" : "@") + label + "](" + destination + ")";
  }
  function format(text, items) {
    var tokens = items.map(token).filter(function (value) { return value && text.indexOf(value) < 0; });
    return text + (tokens.length ? "\n\n" + tokens.join(" ") : "");
  }
  function render(container, text, attachments, options) {
    var pattern = /\[([@#▤][^\]\n]{1,102})\]\((\/[^\s)]+)\)/g;
    var match, cursor = 0;
    var cards=null;
    var safe = new RegExp("^/(?:uploads/" + uuid + "/download|paper/" + uuid + "|collection/" + uuid + "|notes#note-" + uuid + "|goals#goal-" + uuid + "|" + pages.join("|") + ")$");
    while ((match = pattern.exec(text))) {
      if (!safe.test(match[2])) { continue; }
      container.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      var referenced=fromHref(match[2],match[1]);
      var unavailable=referenced && referenced.kind === "attachment" && Array.isArray(attachments) && attachments.some(function(item){return item.document_id===referenced.id && item.available===false;});
      var link = document.createElement(unavailable ? "span" : "a");
      if(!unavailable)link.href=match[2];link.textContent=match[1]+(unavailable ? " (unavailable)" : "");
      link.className = "wick-prompt-reference"; link.target = "_blank"; link.rel = "noopener";
      if(referenced && referenced.kind === "attachment" && !(options && options.inline)) {
        if(!cards){cards=document.createElement("div");cards.className="message-files";}
        link.classList.add("message-file-card");link.removeAttribute("target");if(!unavailable){link.dataset.documentId=referenced.id;link.href="/uploads/"+referenced.id+"/pages/1";}
        link.addEventListener("click",function(event){
          if(window.PrivateUploads && this.dataset.documentId){event.preventDefault();window.PrivateUploads.open(this.dataset.documentId);}
        });
        var metadata=Array.isArray(attachments) && attachments.find(function(item){return item.document_id===referenced.id;});
        var filename=(metadata && metadata.filename) || (window.PrivateUploads && window.PrivateUploads.label && window.PrivateUploads.label(referenced.id)) || match[1].slice(1);
        var extension=filename.split(".").pop().toUpperCase();
        link.title=filename+(unavailable ? " (unavailable)" : "");
        link.replaceChildren();
        var badge=document.createElement("span");badge.className="message-file-icon";badge.textContent=extension.length<=5 ? extension : "FILE";
        var info=document.createElement("span");info.className="message-file-info";
        var title=document.createElement("strong");title.textContent=filename;
        var type=document.createElement("small");type.textContent=unavailable ? "File unavailable" : "Document · "+badge.textContent;
        info.append(title,type);link.append(badge,info);
        cards.appendChild(link);
      } else container.appendChild(link);
      cursor = pattern.lastIndex;
    }
    container.appendChild(document.createTextNode(text.slice(cursor)));
    if(cards)container.prepend(cards);
  }
  window.WickMentions = { href: href, fromHref: fromHref, token: token, format: format, render: render };
})();
