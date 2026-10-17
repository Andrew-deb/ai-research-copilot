/* Keep the chat shell inside the visible viewport when mobile keyboards open. */
(function () {
  "use strict";
  if(!document.getElementById("chat-composer"))return;
  var root=document.documentElement, viewport=window.visualViewport, frame=0;
  function update(){
    frame=0;
    // Pinch zoom should retain native panning rather than resize the application.
    if(viewport && Math.abs(viewport.scale-1)>.01)return;
    var height=viewport ? viewport.height : window.innerHeight;
    var top=viewport ? viewport.offsetTop : 0;
    root.style.setProperty("--chat-view-height",height+"px");
    root.style.setProperty("--chat-view-top",top+"px");
    root.style.setProperty("--chat-keyboard-inset",Math.max(0,window.innerHeight-height-top)+"px");
    root.style.setProperty("--chat-editor-max-height",Math.round(Math.max(64,Math.min(180,height*.28)))+"px");
  }
  function schedule(){if(!frame)frame=requestAnimationFrame(update);}
  if(viewport){viewport.addEventListener("resize",schedule);viewport.addEventListener("scroll",schedule);}
  window.addEventListener("resize",schedule);window.addEventListener("pageshow",schedule);
  update();
})();
