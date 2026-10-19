/* File lifecycle only; prompt text, context mentions and model calls stay separate. */
(function () {
  "use strict";
  const input=document.getElementById("upload-input"), choose=document.getElementById("upload-choose");
  if (!input || !choose) return;
  const page=document.querySelector(".chat-page") || document.querySelector(".landing-main");
  const tray=document.getElementById("private-files"),list=document.getElementById("upload-list"),status=document.getElementById("upload-status");
  let busy=false, timer=null, generation=0, creating=null, currentFiles=[];
  function token() { return document.querySelector('meta[name="csrf-token"]').content; }
  async function request(url,options) {
    const response=await fetch(url,options),body=await response.json();
    if (!response.ok) throw new Error(body.detail || "The file operation could not be completed.");
    return body;
  }
  function mode() {
    const picker=document.getElementById("chat-mode");
    return picker ? picker.value : (page.dataset.surface === "assistant" ? "wick" : "research");
  }
  async function conversation() {
    if (page.dataset.conversation) return page.dataset.conversation;
    if (!creating) creating=request("/uploads/conversations",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":token(),Accept:"application/json"},body:JSON.stringify({mode:mode()})})
      .then(body=>{
        page.dataset.conversation=body.conversation_id;
        document.dispatchEvent(new CustomEvent("uploads:conversation",{detail:{conversation_id:body.conversation_id}}));
        return body.conversation_id;
      }).finally(()=>{creating=null;});
    return creating;
  }
  const labels={uploading:"Uploading",queued:"Waiting to process",processing:"Processing",ready:"Ready",failed:"Failed",deleting:"Deleting",deleted:"Deleted",expired:"Expired"};
  let selected=null, rendered="", previewGeneration=0;
  const resources=window.ChatResources ? window.ChatResources.files(false) : null;
  const library=element("div","conversation-file-list");
  if(resources)resources.append(library);
  const panelButton=element("button","files-panel-button","▤");panelButton.type="button";
  panelButton.hidden=true;
  panelButton.title="Conversation files";panelButton.setAttribute("aria-label","Open conversation files");
  if(resources){choose.parentNode.insertBefore(panelButton,choose);panelButton.addEventListener("click",()=>openLibrary());}
  function iconButton(symbol,label,handler) {
    const button=element("button","file-icon-action");button.type="button";
    const paths={"save":"M6 3h12v18l-6-4-6 4z","close":"M18 6 6 18M6 6l12 12","expand":"M15 3h6v6M10 14 21 3M9 21H3v-6M14 10 3 21","←":"M19 12H5m6-6-6 6 6 6","↗":"M7 17 17 7M7 7h10v10","↻":"M20 7v5h-5M20 12a8 8 0 1 0-2 6","×":"M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"};
    button.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="'+paths[symbol]+'"/></svg>';
    button.title=label;button.setAttribute("aria-label",label);button.addEventListener("click",handler);return button;
  }
  function openLibrary() {
    const previous=library.querySelector(".file-preview-content");
    if(previous && previous.disposePreview)previous.disposePreview();if(window.ChatResources.preview)window.ChatResources.preview(false);selected=null;previewGeneration++;if(resources){window.ChatResources.files(true);renderLibrary();if(window.ChatResources.preview)window.ChatResources.preview(false);}}
  function renderLibrary() {
    if(!resources || selected)return;
    const previous=library.querySelector(".file-preview-content");
    if(previous && previous.disposePreview)previous.disposePreview();
    const heading=element("div","file-menu-heading");heading.append(element("h3","","Files"));
    const files=currentFiles.filter(file=>file.status!=="deleted");
    const available=files.filter(canDownload);
    if(available.length){const all=element("a","file-download-all","↓ Download all");all.href="/uploads/conversations/"+encodeURIComponent(page.dataset.conversation)+"/download";all.setAttribute("download","");heading.append(all);}
    library.replaceChildren(heading,element("p","private-files-note","Unsaved files expire after "+tray.dataset.retention+" days. Saved files remain until deleted and count toward storage limits."));
    const grid=element("div","conversation-file-grid");library.append(grid);
    if(!files.length)library.append(element("p","private-files-note","Upload a file using + to get started."));
    files.forEach(file=>{
      const tile=element("div","conversation-file-tile"),card=element("button","conversation-file");card.type="button";card.title=file.filename;
      card.append(element("strong","private-file-name",file.filename),element("span","private-file-icon",fileType(file)),element("small","private-file-state",file.retained && file.status==="ready" ? "Saved to workspace" : labels[file.status] || file.status));
      card.addEventListener("click",()=>showDetails(file));tile.append(card);
      if(!["deleted","deleting"].includes(file.status)){const remove=iconButton("×","Delete "+file.filename,()=>removeFile(file));remove.classList.add("file-menu-delete");tile.append(remove);}
      grid.append(tile);
    });
  }

  function element(tag,className,text) {
    const node=document.createElement(tag);if(className)node.className=className;
    if(text!==undefined)node.textContent=text;return node;
  }
  function downloadUrl(file) {return "/uploads/"+encodeURIComponent(file.document_id)+"/download";}
  function fileType(file) {return (file.filename.split(".").pop() || "file").toUpperCase();}
  function canDownload(file) {return ["ready","failed","queued","processing"].includes(file.status);}
  function removeFile(file) {
    if(window.confirm("Delete this private file and its extracted text?"))operate(file,"","DELETE");
  }
  async function showPanelPreview(file, pageNumber) {
    const previous=library.querySelector(".file-preview-content");
    if(previous && previous.disposePreview)previous.disposePreview();
    selected=file.document_id;const current=++previewGeneration;
    window.ChatResources.files(true);if(window.ChatResources.preview)window.ChatResources.preview(true);library.replaceChildren();
    const header=element("div","file-preview-toolbar");
    const title=element("h3","file-preview-title",file.filename);title.title=file.filename;header.append(title);
    const actions=element("details","file-preview-more");
    const summary=element("summary","file-icon-action","⋯");summary.title="More file actions";summary.setAttribute("aria-label","More file actions");actions.append(summary);
    const menu=element("div","file-preview-menu");actions.append(menu);
    menu.append(element("p","private-files-note",(labels[file.status] || file.status)+" · "+Math.ceil((file.size_bytes || 0)/1024)+" KB · "+(file.retained ? "Saved to workspace" : "Expires "+(file.expires_at ? new Date(file.expires_at).toLocaleDateString() : "after retention period"))));
    if(file.status==="ready")menu.append(iconButton("↗","Reference in prompt",()=>{actions.open=false;insertReference(file);prompt.focus();}));
    if(file.status==="ready" && !file.retained)menu.append(iconButton("save","Save to workspace",()=>operate(file,"retain","POST")));
    if(file.status==="failed")menu.append(iconButton("↻","Retry processing",()=>operate(file,"retry","POST")));
    if(!["deleted","deleting"].includes(file.status))menu.append(iconButton("×","Delete file",()=>removeFile(file)));
    if(canDownload(file)){
      const download=element("a","file-icon-action","↓");download.href=downloadUrl(file);download.title="Download original";download.setAttribute("aria-label","Download original");header.append(download);
    }
    let expanded=false;
    const expand=iconButton("expand","Expand preview",()=>{expanded=!expanded;if(window.ChatResources.expand)window.ChatResources.expand(expanded);expand.title=expanded ? "Restore preview" : "Expand preview";expand.setAttribute("aria-label",expand.title);expand.setAttribute("aria-pressed",String(expanded));});
    expand.setAttribute("aria-pressed","false");header.append(expand,actions,iconButton("close","Back to files",openLibrary));
    header.addEventListener("keydown",event=>{if(event.key==="Escape" && actions.open){event.preventDefault();event.stopPropagation();actions.open=false;summary.focus();}});
    library.append(header);
    if(file.status!=="ready"){library.append(element("p","upload-preview-message",file.error || "A preview is available after processing finishes."));return;}
    const output=element("div","file-preview-content","Loading preview…");library.append(output);
    try {
      if(fileType(file)==="PDF") {
        window.PdfPreview.mount(output,"/uploads/"+encodeURIComponent(file.document_id)+"/preview",pageNumber || 1);
        const fallback=element("a","","Open extracted text");fallback.href="/uploads/"+encodeURIComponent(file.document_id)+"/pages/"+(pageNumber || 1);fallback.target="_blank";fallback.rel="noopener";menu.append(fallback);
      } else {
        const response=await fetch(downloadUrl(file),{cache:"no-store"});
        if(!response.ok)throw new Error("Preview unavailable. Try again from Files.");
        const text=await response.text();if(current!==previewGeneration)return;
        output.replaceChildren(element("pre","upload-text-preview",text.slice(0,20000)+(text.length>20000 ? "\n… Download the original for the full text." : "")));
      }
    } catch(error){if(current===previewGeneration)output.textContent=error.message;}
  }
  function showDetails(file, pageNumber) {
    if(resources){showPanelPreview(file, pageNumber);return;}
    openLibrary();
  }
  const prompt=document.getElementById("chat-input");
  function selectedIds() {
    const ids=[], pattern=/\[▤[^\]\n]+\]\(\/uploads\/([0-9a-f-]{36})\/download\)/gi;
    let match;while((match=pattern.exec(prompt.value)))if(!ids.includes(match[1]))ids.push(match[1]);
    return ids;
  }
  window.PrivateUploads={select:async function(file,range){
      await conversation();
      const value={kind:"attachment",id:file.document_id,label:file.filename};
      if(!currentFiles.some(item=>item.document_id===file.document_id))currentFiles.push(file);
      if(window.WickEditor && prompt.hidden)window.WickEditor.insert(value,range);
      else insertReference(file);
      rendered="";render(currentFiles);
    },label:function(id){const file=currentFiles.find(item=>item.document_id===id);return file ? file.filename : null;},references:selectedIds,open:async function(id,pageNumber){
      if(resources)window.ChatResources.files(true);
      let file=currentFiles.find(item=>item.document_id===id);
      if(!file){await refresh();file=currentFiles.find(item=>item.document_id===id);}
      if(!file){try{file=await request("/uploads/"+encodeURIComponent(id)+"/metadata",{headers:{Accept:"application/json"}});currentFiles.push(file);}catch(error){}}
      if(file){if(window.ChatResources.setFileCount)window.ChatResources.setFileCount(currentFiles.filter(item=>item.status!=="deleted").length);showDetails(file,pageNumber);}
      else {openLibrary();library.append(element("p","upload-details-error","This file is unavailable. It may have expired or been deleted."));}
    },
    assets:function(query){return currentFiles.filter(file=>file.status==="ready" && file.filename.toLowerCase().includes((query || "").toLowerCase())).map(file=>({kind:"attachment",id:file.document_id,label:file.filename}));},insert:insertReference};
  function insertReference(file) {
    const item={kind:"attachment",id:file.document_id,label:file.filename};
    if(window.WickEditor && prompt.hidden)window.WickEditor.insert(item);
    else {
      const label=file.filename.replace(/[\[\]\\\r\n]/g," ").slice(0,100);
      const token="[▤"+label+"]("+downloadUrl(file)+") ";
      const start=prompt.selectionStart || 0,end=prompt.selectionEnd || start;
      prompt.value=prompt.value.slice(0,start)+token+prompt.value.slice(end);
      prompt.focus();prompt.setSelectionRange(start+token.length,start+token.length);
      prompt.dispatchEvent(new Event("input",{bubbles:true}));
    }
  }
  function render(items) {
    currentFiles=items;
    const fileCount=items.filter(file=>file.status!=="deleted").length;
    panelButton.hidden=fileCount===0;
    if(resources && window.ChatResources.setFileCount)window.ChatResources.setFileCount(fileCount);
    const signature=JSON.stringify([items,busy]);if(signature===rendered)return;rendered=signature;
    list.replaceChildren();
    items.filter(file=>file.status!=="deleted" && selectedIds().includes(file.document_id)).forEach(file=>{
      const row=element("div","private-file");row.dataset.documentId=file.document_id;
      const card=element("button","private-file-open");card.type="button";
      card.setAttribute("aria-label",file.filename+", "+(labels[file.status] || file.status)+". Open file details");
      card.title=file.filename;
      card.append(element("span","private-file-icon",fileType(file)));
      const info=element("span","private-file-info");
      info.append(element("span","private-file-name",file.filename));
      info.append(element("span","private-file-state",labels[file.status] || file.status));
      card.append(info);card.addEventListener("click",()=>showDetails(file));row.append(card);
      if(!["deleted","deleting"].includes(file.status)) {
        const remove=element("button","private-file-remove","×");remove.type="button";remove.disabled=busy;
        remove.title="Remove from prompt";remove.setAttribute("aria-label","Remove from prompt: "+file.filename);
        remove.addEventListener("click",()=>{prompt.value=prompt.value.replace(/\[▤[^\]\n]+\]\(\/uploads\/([0-9a-f-]{36})\/download\) ?/gi,(text,id)=>id===file.document_id ? "" : text);prompt.dispatchEvent(new Event("input",{bubbles:true}));});row.append(remove);
      }
      list.append(row);
    });
    if(resources){
      if(selected && !items.some(file=>file.document_id===selected && file.status==="ready")){selected=null;previewGeneration++;if(window.ChatResources.preview)window.ChatResources.preview(false);}
      renderLibrary();
    }

  }
  async function refresh() {
    clearTimeout(timer);
    const current=++generation;
    const id=page.dataset.conversation;
    if (!id) {render([]);return;}
    try {
      const body=await request("/uploads/conversations/"+encodeURIComponent(id),{headers:{Accept:"application/json"}});
      if (current!==generation) return;
      render(body.items);
      if(!busy && status.dataset.kind!=="error")status.textContent="";
      if (body.items.some(file=>["uploading","queued","processing","deleting"].includes(file.status))) timer=setTimeout(refresh,3000);
    } catch (error) { status.dataset.kind="error";status.textContent=error.message; timer=setTimeout(refresh,15000); }
  }
  async function operate(file,suffix,method) {
    if (busy) return;
    busy=true;choose.disabled=true;status.dataset.kind="progress";
    try {
      await request("/uploads/"+encodeURIComponent(file.document_id)+(suffix ? "/"+suffix : ""),{method:method,headers:{"X-CSRFToken":token(),Accept:"application/json"}});
      if(suffix==="retain") {
        const save=library.querySelector('[aria-label="Save to workspace"]');if(save)save.remove();
        const note=library.querySelector('.file-preview-menu .private-files-note');if(note)note.textContent="Ready · Saved to workspace · Kept until deleted";
      }
      if(!suffix && method==="DELETE") {
        prompt.value=prompt.value.replace(/\[▤[^\]\n]+\]\(\/uploads\/([0-9a-f-]{36})\/download\) ?/gi,(token,id)=>id===file.document_id ? "" : token);
        prompt.dispatchEvent(new Event("input",{bubbles:true}));
      }
      status.textContent=suffix==="retain" ? "Saved to workspace. This file will not expire automatically." : suffix ? "Retry queued." : "Deletion queued. The file is unavailable immediately.";
    } catch (error) { status.dataset.kind="error";status.textContent=error.message; }
    finally {busy=false;choose.disabled=false;refresh();}
  }
  choose.addEventListener("click",()=>{
    const menu=document.getElementById("wick-context-menu");if(menu)menu.hidden=true;
    input.click();
  });
  input.addEventListener("change",async()=>{
    if(busy)return;
    const files=Array.from(input.files);input.value="";
    if(!files.length)return;
    if(files.length>3 || files.some(file=>file.size>Number(tray.dataset.maxMib)*1024*1024 || !/\.(pdf|txt|md)$/i.test(file.name))) {
      status.dataset.kind="error";status.textContent="Choose up to three PDF, TXT or Markdown files, each up to "+tray.dataset.maxMib+" MiB.";return;
    }
    busy=true;choose.disabled=true;status.dataset.kind="progress";
    try {
      const id=await conversation();
      for (const file of files) {
        status.textContent="Uploading "+file.name+"…";
        const form=new FormData();form.append("file",file);form.append("conversation_id",id);
        const body=await request("/uploads",{method:"POST",headers:{"X-CSRFToken":token(),Accept:"application/json"},body:form});
        insertReference(body);render([body,...currentFiles]);
      }
      status.textContent="Upload complete.";
    } catch(error) { status.dataset.kind="error";status.textContent=error.message+" Earlier successful uploads are kept."; }
    finally {busy=false;choose.disabled=false;refresh();}
  });
  prompt.addEventListener("input",()=>{rendered="";render(currentFiles);});
  document.addEventListener("uploads:sent",()=>{rendered="";render(currentFiles);});
  document.addEventListener("wick:conversation",()=>{selected=null;previewGeneration++;currentFiles=[];render([]);refresh();});
  document.addEventListener("click",event=>{const link=event.target.closest('a[href^="/uploads/"]');if(event.defaultPrevented || !link || !resources || resources.contains(link) || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)return;
    const match=/^\/uploads\/([0-9a-f-]{36})\/(download|pages\/\d+)$/.exec(link.getAttribute("href"));
    if(match){event.preventDefault();window.PrivateUploads.open(match[1],match[2].startsWith("pages/") ? Number(match[2].split("/")[1]) : 1);}
  });
  window.addEventListener("pagehide",()=>{clearTimeout(timer);generation++;const preview=library.querySelector(".file-preview-content");if(preview && preview.disposePreview)preview.disposePreview();});
  const picker=document.getElementById("chat-mode");
  function categoryVisibility() {
    document.querySelectorAll("[data-context-category]").forEach(button=>{button.hidden=mode()!=="wick" && button.dataset.contextCategory!=="assets";});
  }
  if(picker)picker.addEventListener("change",categoryVisibility);
  categoryVisibility();
  render([]);
  refresh();
})();
