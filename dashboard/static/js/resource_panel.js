/* Resource rail geometry only; file state and transport live in uploads.js. */
(function () {
  "use strict";
  window.ResourcePanel = {mount:function(rail, layout) {
    var wrapper=layout || rail.parentElement, dragging=false, width=360;
    var handle=document.createElement("div");handle.className="resource-resize";handle.tabIndex=0;
    handle.setAttribute("role","separator");handle.setAttribute("aria-orientation","vertical");
    handle.setAttribute("aria-label","Resize sources and files panel");handle.title="Drag to resize; use arrow keys when focused";
    rail.prepend(handle);
    function bounds(){return Math.max(280,Math.min(wrapper.getBoundingClientRect().width-444,window.innerWidth*.55));}
    function setWidth(value){width=Math.max(280,Math.min(value,bounds()));rail.style.setProperty("--resource-width",width+"px");handle.setAttribute("aria-valuemin","280");handle.setAttribute("aria-valuemax",String(Math.round(bounds())));handle.setAttribute("aria-valuenow",String(Math.round(width)));}
    handle.addEventListener("pointerdown",function(e){if(e.button!==0)return;dragging=true;handle.setPointerCapture(e.pointerId);rail.classList.add("is-resizing");e.preventDefault();});
    handle.addEventListener("pointermove",function(e){if(dragging)setWidth(rail.getBoundingClientRect().right-e.clientX);});
    function end(){dragging=false;rail.classList.remove("is-resizing");}
    handle.addEventListener("pointerup",end);handle.addEventListener("pointercancel",end);handle.addEventListener("lostpointercapture",end);
    handle.addEventListener("keydown",function(e){if(e.key!=="ArrowLeft" && e.key!=="ArrowRight" && e.key!=="Home" && e.key!=="End")return;e.preventDefault();setWidth(e.key==="Home" ? 280 : e.key==="End" ? bounds() : width+(e.key==="ArrowLeft" ? 24 : -24));});
    document.addEventListener("keydown",function(e){if(e.key==="Escape" && rail.classList.contains("is-expanded")){e.preventDefault();e.stopImmediatePropagation();var restore=rail.querySelector('[aria-label="Restore preview"]');if(restore)restore.click();else rail.classList.remove("is-expanded");}},true);
    window.addEventListener("resize",function(){setWidth(width);});setWidth(width);
    return {expand:function(value){rail.classList.toggle("is-expanded",value);},preview:function(value){rail.classList.toggle("has-file-preview",value);if(!value)rail.classList.remove("is-expanded");}};
  }};
})();
