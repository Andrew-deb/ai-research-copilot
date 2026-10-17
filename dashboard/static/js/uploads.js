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
  const dialog=document.getElementById("upload-details");
  let selected=null, rendered="";
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
  function showDetails(file) {
    if(!dialog)return;
    selected=file.document_id;
    document.getElementById("upload-details-title").textContent=file.filename;
    const body=document.getElementById("upload-details-body"),actions=document.getElementById("upload-details-actions");
    body.replaceChildren();actions.replaceChildren();
    const size=Number.isFinite(file.size_bytes) ? " · "+Math.max(1,Math.ceil(file.size_bytes/1024))+" KB" : "";
    body.append(element("p","upload-details-meta",fileType(file)+size+" · "+(labels[file.status] || file.status)));
    if(file.page_count)body.append(element("p","upload-details-meta",file.page_count+" pages processed"));
    if(file.error)body.append(element("p","upload-details-error",file.error));
    if(canDownload(file)) {
      const link=element("a","upload-download","Download original");link.href=downloadUrl(file);actions.append(link);
      if(["TXT","MD"].includes(fileType(file))) {
        const preview=element("button","","Preview text");preview.type="button";
        preview.addEventListener("click",async()=>{
          preview.disabled=true;
          const output=element("pre","upload-text-preview","Loading preview…");body.append(output);
          try {
            const response=await fetch(downloadUrl(file),{cache:"no-store"});
            if(!response.ok)throw new Error("The file preview is unavailable.");
            const text=await response.text();output.textContent=text.slice(0,20000)+(text.length>20000 ? "\n… Preview truncated. Download the original for the full text." : "");
          }catch(error){output.textContent=error.message;}
        });actions.append(preview);
      }
    }
    if(file.status==="failed") {
      const retry=element("button","","Retry processing");retry.type="button";retry.disabled=busy;
      retry.addEventListener("click",()=>operate(file,"retry","POST"));actions.append(retry);
    }
    if(!["deleted","deleting"].includes(file.status)) {
      const remove=element("button","upload-delete","Delete file");remove.type="button";remove.disabled=busy;
      remove.addEventListener("click",()=>removeFile(file));actions.append(remove);
    }
    if(!dialog.open)dialog.showModal();
  }
  if(dialog) {
    document.getElementById("upload-details-close").addEventListener("click",()=>dialog.close());
    dialog.addEventListener("close",()=>{selected=null;document.getElementById("upload-details-body").replaceChildren();});
  }
  function render(items) {
    currentFiles=items;
    const signature=JSON.stringify([items,busy]);if(signature===rendered)return;rendered=signature;
    list.replaceChildren();
    items.filter(file=>file.status!=="deleted").forEach(file=>{
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
        remove.title="Delete file";remove.setAttribute("aria-label","Delete "+file.filename);
        remove.addEventListener("click",()=>removeFile(file));row.append(remove);
      }
      list.append(row);
    });
    if(dialog && dialog.open && selected) {
      const file=items.find(item=>item.document_id===selected);
      if(!file || ["deleted","deleting"].includes(file.status))dialog.close();
      else showDetails(file);
    }
  }
  async function refresh() {
    clearTimeout(timer);
    const id=page.dataset.conversation;
    if (!id) return;
    const current=++generation;
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
      status.textContent=suffix ? "Retry queued." : "Deletion queued. The file is unavailable immediately.";
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
        render([body,...currentFiles]);
      }
      status.textContent="Upload complete.";
    } catch(error) { status.dataset.kind="error";status.textContent=error.message+" Earlier successful uploads are kept."; }
    finally {busy=false;choose.disabled=false;refresh();}
  });
  document.addEventListener("wick:conversation",refresh);
  window.addEventListener("pagehide",()=>{clearTimeout(timer);generation++;});
  const picker=document.getElementById("chat-mode");
  function categoryVisibility() {
    document.querySelectorAll("[data-context-category]").forEach(button=>{button.hidden=mode()!=="wick";});
  }
  if(picker)picker.addEventListener("change",categoryVisibility);
  categoryVisibility();
  refresh();
})();
