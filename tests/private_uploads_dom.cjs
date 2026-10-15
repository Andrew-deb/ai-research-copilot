/* Real upload UI preserves the prompt, binds a conversation, and renders safely. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const dom=new JSDOM(`<meta name="csrf-token" content="token"><main class="chat-page" data-conversation=""><form id="chat-composer"><textarea id="chat-input">Keep my prompt</textarea><button id="chat-mode" value="research"></button></form>
<button id="upload-choose"></button><input type="file" id="upload-input"><div id="private-files" data-max-mib="10"><div id="upload-list"></div><div id="upload-status"></div></div><button data-context-category="assets"></button></main>`,{url:'https://alfred.example',runScripts:'outside-only'});
const w=dom.window,d=w.document,requests=[],created=[];
const file={document_id:'00000000-0000-0000-0000-000000000001',filename:'<img src=x onerror=alert(1)>.txt',status:'ready'};
w.fetch=async(url,options)=>{
 requests.push([url,options]);
 return {ok:true,json:async()=>url==='/uploads/conversations'?{conversation_id:'owned-conversation'}:url==='/uploads'?file:{items:[file]}};
};
w.confirm=()=>true;
d.addEventListener('uploads:conversation',event=>created.push(event.detail.conversation_id));
w.eval(fs.readFileSync(path.resolve(__dirname,'../dashboard/static/js/uploads.js'),'utf8'));
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{
 assert.equal(d.querySelector('[data-context-category]').hidden,true);
 const input=d.getElementById('upload-input');
 Object.defineProperty(input,'files',{value:[new w.File(['notes'],'notes.txt',{type:'text/plain'})],configurable:true});
 input.dispatchEvent(new w.Event('change'));await settle();await settle();
 assert.deepEqual(created,['owned-conversation']);
 assert.equal(d.querySelector('.chat-page').dataset.conversation,'owned-conversation');
 assert.equal(d.getElementById('chat-input').value,'Keep my prompt');
 assert.equal(requests[1][1].headers['X-CSRFToken'],'token');
 assert.equal(d.querySelector('#upload-list img'),null);
 assert.ok(d.getElementById('upload-list').textContent.includes(file.filename));
 assert.equal(d.querySelector('#upload-list a').getAttribute('href'),'/uploads/'+file.document_id+'/download');
 const picker=d.getElementById('chat-mode');picker.value='wick';picker.dispatchEvent(new w.Event('change'));
 assert.equal(d.querySelector('[data-context-category]').hidden,false);
 const before=requests.length;
 Object.defineProperty(input,'files',{value:[new w.File(['bad'],'program.exe')],configurable:true});
 input.dispatchEvent(new w.Event('change'));await settle();
 assert.equal(requests.length,before);assert.match(d.getElementById('upload-status').textContent,/PDF, TXT or Markdown/);
 w.dispatchEvent(new w.Event('pagehide'));dom.window.close();
 console.log('Private upload DOM: prompt preserved, conversation bound, filename safe, invalid file refused.');
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
