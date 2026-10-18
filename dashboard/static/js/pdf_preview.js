/* PDF pages scroll in the resource panel, never in a browser plug-in.
 * Only visible pages retain bitmap memory; closing a preview destroys its worker.
 */
(function () {
  "use strict";
  let library;
  window.PdfPreview = {mount: async function (host, url, startPage) {
    let stopped=false, task, pdf, observer;
    const entries=[], active=new Set();
    function dispose() {
      if(stopped)return;stopped=true;
      if(observer)observer.disconnect();
      active.forEach(render=>render.cancel());
      entries.forEach(entry=>{if(entry.canvas){entry.canvas.width=entry.canvas.height=0;}});
      if(task)task.destroy();
    }
    // The caller can cancel even while the module or private bytes are loading.
    host.disposePreview=dispose;
    host.classList.add("pdf-pages");host.tabIndex=0;
    host.setAttribute("role","region");host.setAttribute("aria-label","PDF document preview");
    try {
      library=library || import("/static/vendor/pdfjs/pdf.min.mjs");
      const engine=await library;if(stopped)return;
      engine.GlobalWorkerOptions.workerSrc="/static/vendor/pdfjs/pdf.worker.min.mjs";
      task=engine.getDocument({url:url,isEvalSupported:false,enableXfa:false,
        disableAutoFetch:true,disableStream:true,disableRange:true});
      pdf=await task.promise;if(stopped)return;
      const first=await pdf.getPage(1), initial=first.getViewport({scale:1});
      host.replaceChildren();
      for(let number=1;number<=pdf.numPages;number++){
        const sheet=document.createElement("div");sheet.className="pdf-page";
        sheet.style.aspectRatio=initial.width+" / "+initial.height;
        sheet.dataset.page=String(number);sheet.setAttribute("aria-label","Page "+number+" of "+pdf.numPages);
        host.append(sheet);entries.push({sheet,number,visible:false,rendering:false,canvas:null});
      }
      async function render(entry){
        if(stopped || !entry.visible || entry.rendering || entry.canvas)return;
        entry.rendering=true;let renderTask;
        try {
          const page=await pdf.getPage(entry.number);if(stopped || !entry.visible)return;
          const base=page.getViewport({scale:1});
          const width=Math.max(1,entry.sheet.clientWidth || 600);
          const ratio=Math.min(window.devicePixelRatio || 1,2);
          // Bound each bitmap independently, including unusually tall pages.
          const scale=Math.min(width*ratio/base.width,Math.sqrt(4000000/(base.width*base.height)));
          const viewport=page.getViewport({scale});
          const canvas=document.createElement("canvas");canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);
          canvas.setAttribute("role","img");canvas.setAttribute("aria-label",entry.sheet.getAttribute("aria-label"));
          entry.sheet.style.aspectRatio=base.width+" / "+base.height;
          entry.canvas=canvas;entry.sheet.replaceChildren(canvas);
          renderTask=page.render({canvasContext:canvas.getContext("2d"),viewport});active.add(renderTask);
          await renderTask.promise;
        } catch(error){if(!stopped && entry.visible)entry.sheet.textContent="Page unavailable. Use Open extracted text or download the original.";}
        finally {
          if(renderTask)active.delete(renderTask);entry.rendering=false;
          if(!entry.visible && entry.canvas){entry.canvas.width=entry.canvas.height=0;entry.canvas=null;entry.sheet.replaceChildren();}
        }
      }
      observer=new IntersectionObserver(changes=>{
        changes.forEach(change=>{
          const entry=entries[Number(change.target.dataset.page)-1];entry.visible=change.isIntersecting;
          if(entry.visible)render(entry);
          else if(entry.canvas && !entry.rendering){entry.canvas.width=entry.canvas.height=0;entry.canvas=null;entry.sheet.replaceChildren();}
        });
      },{root:host,rootMargin:"300px 0px"});
      entries.forEach(entry=>observer.observe(entry.sheet));
      const target=entries[Math.max(0,Math.min(entries.length-1,(startPage || 1)-1))];
      host.scrollTop=target.sheet.offsetTop-host.firstElementChild.offsetTop;
    } catch(error){if(!stopped)host.textContent="PDF preview unavailable. Use Open extracted text or download the original.";}
  }};
})();
