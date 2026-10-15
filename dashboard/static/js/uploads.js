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
  function render(items) {
    currentFiles=items;list.replaceChildren();
    items.filter(file=>file.status!=="deleted").forEach(file=>{
      const row=document.createElement("div");row.className="private-file";
      const label=document.createElement("span");label.textContent=file.filename+" · "+(labels[file.status] || file.status);
      row.append(label);
      if (["ready","failed","queued","processing"].includes(file.status)) {
        const link=document.createElement("a");link.href="/uploads/"+encodeURIComponent(file.document_id)+"/download";link.textContent="Download";row.append(link);
      }
      if (file.error) {const error=document.createElement("span");error.textContent=file.error;row.append(error);}
      if (file.status==="failed") {
        const retry=document.createElement("button");retry.type="button";retry.textContent="Retry";retry.disabled=busy;
        retry.addEventListener("click",()=>operate(file,"retry","POST"));row.append(retry);
      }
      if (!["deleted","deleting"].includes(file.status)) {
        const remove=document.createElement("button");remove.type="button";remove.textContent="Delete file";remove.disabled=busy;
        remove.addEventListener("click",()=>{
          if (window.confirm("Delete this private file and its extracted text?")) operate(file,"","DELETE");
        });row.append(remove);
      }
      list.append(row);
    });
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
      if (body.items.some(file=>["uploading","queued","processing","deleting"].includes(file.status))) timer=setTimeout(refresh,3000);
    } catch (error) { status.textContent=error.message; timer=setTimeout(refresh,15000); }
  }
  async function operate(file,suffix,method) {
    if (busy) return;
    busy=true;choose.disabled=true;
    try {
      await request("/uploads/"+encodeURIComponent(file.document_id)+(suffix ? "/"+suffix : ""),{method:method,headers:{"X-CSRFToken":token(),Accept:"application/json"}});
      status.textContent=suffix ? "Retry queued." : "Deletion queued. The file is unavailable immediately.";
    } catch (error) { status.textContent=error.message; }
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
      status.textContent="Choose up to three PDF, TXT or Markdown files, each up to "+tray.dataset.maxMib+" MiB.";return;
    }
    busy=true;choose.disabled=true;
    try {
      const id=await conversation();
      for (const file of files) {
        status.textContent="Uploading "+file.name+"…";
        const form=new FormData();form.append("file",file);form.append("conversation_id",id);
        const body=await request("/uploads",{method:"POST",headers:{"X-CSRFToken":token(),Accept:"application/json"},body:form});
        render([body,...currentFiles]);
      }
      status.textContent="Upload complete. Text processing is queued.";
    } catch(error) { status.textContent=error.message+" Earlier successful uploads are kept."; }
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
