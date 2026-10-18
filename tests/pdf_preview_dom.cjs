/* Exercise visibility, page targeting, bounded canvases and worker cleanup. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const dom=new JSDOM('<div id="preview">Loading</div>',{runScripts:'outside-only'}),w=dom.window;
let callback,destroyed=0,disconnected=0,renders=0;
w.IntersectionObserver=class{constructor(cb,options){callback=cb;assert.equal(options.root.id,'preview');}observe(){}disconnect(){disconnected++;}};
w.HTMLCanvasElement.prototype.getContext=()=>({});
const pdf={numPages:3,getPage:async()=>({getViewport:({scale})=>({width:600*scale,height:800*scale}),render:()=>{renders++;return {promise:Promise.resolve(),cancel(){}};}})};
const engine={GlobalWorkerOptions:{},getDocument(options){assert.equal(options.isEvalSupported,false);assert.equal(options.disableRange,true);return {promise:Promise.resolve(pdf),destroy(){destroyed++;return Promise.resolve();}};}};
w.mockEngine=engine;
const source=fs.readFileSync('dashboard/static/js/pdf_preview.js','utf8').replace('import("/static/vendor/pdfjs/pdf.min.mjs")','Promise.resolve(window.mockEngine)');w.eval(source);
(async()=>{
 const host=w.document.getElementById('preview');await w.PdfPreview.mount(host,'/uploads/owned/preview',2);
 assert.equal(host.querySelectorAll('.pdf-page').length,3);assert.equal(host.querySelector('iframe'),null);
 assert.equal(host.tabIndex,0);assert.equal(engine.GlobalWorkerOptions.workerSrc,'/static/vendor/pdfjs/pdf.worker.min.mjs');
 const sheets=host.querySelectorAll('.pdf-page');
 callback([{target:sheets[1],isIntersecting:true}]);await new Promise(r=>setTimeout(r,0));
 assert.equal(renders,1);assert.ok(sheets[1].querySelector('canvas'));assert.equal(sheets[0].querySelector('canvas'),null);
 callback([{target:sheets[1],isIntersecting:false}]);assert.equal(sheets[1].querySelector('canvas'),null);
 callback([{target:sheets[2],isIntersecting:true}]);await new Promise(r=>setTimeout(r,0));
 host.disposePreview();host.disposePreview();assert.equal(destroyed,1);assert.equal(disconnected,1);
 assert.equal(sheets[2].querySelector('canvas').width,0);
 dom.window.close();console.log('PDF preview uses panel pages, lazy rendering and deterministic cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;dom.window.close();});
