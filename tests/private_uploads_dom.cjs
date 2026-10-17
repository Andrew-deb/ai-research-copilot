/* Real upload UI preserves the prompt, binds a conversation, and renders safely. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const dom=new JSDOM(`<meta name="csrf-token" content="token"><main class="chat-page" data-conversation=""><form id="chat-composer"><textarea id="chat-input">Keep my prompt</textarea><button id="chat-mode" value="research"></button></form>
<button id="upload-choose"></button><input type="file" id="upload-input"><div id="private-files" data-max-mib="10"><div id="upload-list"></div><div id="upload-status"></div></div><button data-context-category="assets"></button><dialog id="upload-details"><h2 id="upload-details-title"></h2><button id="upload-details-close"></button><div id="upload-details-body"></div><div id="upload-details-actions"></div></dialog></main>`,{url:'https://alfred.example',runScripts:'outside-only'});
const w=dom.window,d=w.document,requests=[],created=[];
const file={document_id:'00000000-0000-0000-0000-000000000001',filename:'<img src=x onerror=alert(1)>.txt',status:'ready'};
const resources=d.createElement('aside');d.body.append(resources);w.ChatResources={files:()=>resources};
w.fetch=async(url,options)=>{
 requests.push([url,options]);
 return {ok:true,text:async()=>'<script>unsafe</script>',json:async()=>url==='/uploads/conversations'?{conversation_id:'owned-conversation'}:url==='/uploads'?file:{items:[file]}};
};
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
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
 assert.ok(d.getElementById('chat-input').value.includes('Keep my prompt'));
 assert.equal(requests[1][1].headers['X-CSRFToken'],'token');
 assert.equal(d.querySelector('#upload-list img'),null);
 assert.ok(d.getElementById('upload-list').textContent.includes(file.filename));
 assert.equal(d.querySelector('#upload-list a'),null);
 assert.equal(d.querySelector('.private-file-open').getAttribute('type'),'button');
 assert.match(d.querySelector('.private-file-remove').getAttribute('aria-label'),/Remove from prompt/);
 assert.equal(d.getElementById('upload-status').textContent,'');
 const prompt=d.getElementById('chat-input');
 assert.deepEqual(Array.from(w.PrivateUploads.references()),[file.document_id]);
 assert.equal(d.querySelector('.private-file-attach'),null);
 // Draft removal is deselection, never a storage deletion.
 d.querySelector('.private-file-remove').click();
 assert.equal(d.querySelector('.private-file'),null);
 assert.equal(requests.some(([,options])=>options && options.method==='DELETE'),false);
 prompt.value='Keep my prompt';prompt.setSelectionRange(5,5);w.PrivateUploads.insert({document_id:file.document_id,filename:'notes.txt'});
 assert.ok(prompt.value.startsWith('Keep [▤'));
 assert.ok(prompt.value.endsWith('my prompt'));
 // A sent message clears draft cards, while the library still contains its file.
 prompt.value='';d.dispatchEvent(new w.Event('uploads:sent'));
 assert.equal(d.querySelector('.private-file'),null);
 assert.ok(resources.querySelector('.conversation-file'));
 assert.ok(resources.querySelector('.file-download-all').href.endsWith('/uploads/conversations/owned-conversation/download'));
 assert.ok(resources.querySelector('.file-menu-delete').getAttribute('aria-label').startsWith('Delete '));
 resources.querySelector('.conversation-file').click();await settle();
 assert.equal(resources.querySelector('h3').textContent,file.filename);
 const expand=resources.querySelector('[aria-label="Expand preview"]');expand.click();
 assert.equal(expand.getAttribute('aria-label'),'Restore preview');expand.click();
 assert.equal(expand.getAttribute('aria-label'),'Expand preview');
 assert.equal(resources.querySelector('pre').textContent,'<script>unsafe</script>');
 assert.equal(resources.querySelector('script'),null);
 assert.equal(resources.querySelector('[aria-label="Download original"]').getAttribute('href'),'/uploads/'+file.document_id+'/download');
 const toolbar=resources.querySelector('.file-preview-toolbar');
 assert.equal(toolbar.children.length,5); // title + download, expand, more, close
 assert.equal(toolbar.querySelector('.file-preview-menu .private-files-note').textContent.includes('Ready'),true);
 assert.equal(resources.querySelector('.file-preview-content > a'),null);
 assert.equal(resources.querySelector('details').open,false);
 resources.querySelector('[aria-label="Back to files"]').click();
 assert.ok(resources.querySelector('.conversation-file'));
 file.filename='A deliberately long document filename.pdf';
 w.PrivateUploads.open(file.document_id);
 assert.match(resources.querySelector('iframe').getAttribute('src'),/view=FitH&zoom=page-width&toolbar=0&navpanes=0/);
 assert.ok(resources.querySelector('.file-preview-menu a').href.endsWith('/pages/1'));
 assert.equal(resources.querySelector('.file-preview-content').children.length,1);
 const picker=d.getElementById('chat-mode');picker.value='wick';picker.dispatchEvent(new w.Event('change'));
 assert.equal(d.querySelector('[data-context-category]').hidden,false);
 const before=requests.length;
 Object.defineProperty(input,'files',{value:[new w.File(['bad'],'program.exe')],configurable:true});
 input.dispatchEvent(new w.Event('change'));await settle();
 assert.equal(requests.length,before);assert.match(d.getElementById('upload-status').textContent,/PDF, TXT or Markdown/);
 resources.querySelector('[aria-label="Delete file"]').click();await settle();await settle();
 const deletion=requests.find(([url,options])=>options && options.method==='DELETE');
 assert.ok(deletion);assert.equal(deletion[1].headers['X-CSRFToken'],'token');
 assert.equal(d.getElementById('chat-input').value,'');
 // A card clicked while conversation metadata is loading waits for the list.
 d.dispatchEvent(new w.Event('wick:conversation'));
 const preview=w.PrivateUploads.open(file.document_id);await preview;await settle();
 assert.ok(resources.querySelector('iframe'));
 w.dispatchEvent(new w.Event('pagehide'));dom.window.close();
 console.log('Private upload DOM: prompt preserved, conversation bound, filename safe, invalid file refused.');
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
