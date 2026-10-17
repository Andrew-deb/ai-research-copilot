/* Simulate visual viewport keyboard changes without changing drafts or scroll. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<form id="chat-composer"><textarea>Keep my draft</textarea></form><div id="chat-thread"></div>',{url:'https://alfred.example/chat',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,v=new w.EventTarget();Object.assign(v,{height:800,offsetTop:0,scale:1});
Object.defineProperty(w,'visualViewport',{value:v});
w.eval(fs.readFileSync(path.join(__dirname,'../dashboard/static/js/chat_viewport.js'),'utf8'));
const value=key=>d.documentElement.style.getPropertyValue(key),tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
 assert.equal(value('--chat-view-height'),'800px');
 d.getElementById('chat-thread').scrollTop=170;
 v.height=350;v.offsetTop=12;v.dispatchEvent(new w.Event('resize'));await tick();
 assert.equal(value('--chat-view-height'),'350px');assert.equal(value('--chat-view-top'),'12px');
 assert.equal(value('--chat-editor-max-height'),'98px');
 assert.equal(d.querySelector('textarea').value,'Keep my draft');assert.equal(d.getElementById('chat-thread').scrollTop,170);
 v.scale=2;v.height=150;v.dispatchEvent(new w.Event('scroll'));await tick();assert.equal(value('--chat-view-height'),'350px');
 v.scale=1;v.height=800;v.offsetTop=0;v.dispatchEvent(new w.Event('resize'));await tick();assert.equal(value('--chat-view-height'),'800px');
 console.log('Chat viewport: keyboard resize, offset, pinch zoom and draft/scroll preservation passed.');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1;});
