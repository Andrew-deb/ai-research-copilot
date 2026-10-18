/* Saved-document browsing and deletion; lifecycle authorization stays server-side. */
(function () {
  "use strict";
  const status=document.getElementById("document-status");
  if(!status)return;
  document.querySelectorAll("[data-delete-document]").forEach(button=>{
    button.addEventListener("click",async()=>{
      if(!window.confirm("Delete this private file and its extracted text? This also makes its chat references unavailable."))return;
      button.disabled=true;
      try {
        const response=await fetch("/uploads/"+encodeURIComponent(button.dataset.deleteDocument),{
          method:"DELETE",headers:{"X-CSRFToken":document.querySelector('meta[name="csrf-token"]').content,Accept:"application/json"}});
        if(!response.ok)throw new Error("Could not delete this file. Try again.");
        const preview=document.getElementById("saved-document-preview");
        if(preview){closed=true;if(preview.disposePreview)preview.disposePreview();preview.textContent="This document has been deleted.";}
        const card=button.closest(".document-card");if(card)card.remove();
        status.textContent="File unavailable immediately. Storage cleanup is queued.";
      }catch(error){status.textContent=error.message;button.disabled=false;}
    });
  });
  const container=document.querySelector(".saved-document"), preview=document.getElementById("saved-document-preview");
  if(!container || !preview)return;
  const url="/uploads/"+encodeURIComponent(container.dataset.documentId);
  let closed=false;
  window.addEventListener("pagehide",()=>{closed=true;if(preview.disposePreview)preview.disposePreview();});
  if(/\.pdf$/i.test(container.dataset.filename))window.PdfPreview.mount(preview,url+"/preview",1);
  else fetch(url+"/download",{cache:"no-store"}).then(response=>{
    if(!response.ok)throw new Error("Preview unavailable. Return to Documents and try again.");
    return response.text();
  }).then(text=>{
    if(closed || document.querySelector('[data-delete-document]:disabled'))return;
    const pre=document.createElement("pre");pre.textContent=text;preview.replaceChildren(pre);
  }).catch(error=>{if(!closed)preview.textContent=error.message;});
})();
