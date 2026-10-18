/* Saved-file deletion uses CSRF and cancels the displayed preview. */
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const source=fs.readFileSync('dashboard/static/js/documents.js','utf8');
const dom=new JSDOM(`<meta name="csrf-token" content="token"><section class="saved-document" data-document-id="owned-id" data-filename="notes.txt"><p id="document-status"></p><button data-delete-document="owned-id"></button><div id="saved-document-preview"></div></section>`,{url:'https://alfred.example/documents/owned-id',runScripts:'outside-only'});
const w=dom.window,d=w.document,requests=[];
w.confirm=()=>true;
w.fetch=async(url,options)=>{requests.push([url,options]);return {ok:true,text:async()=>'<script>private</script>'};};
w.eval(source);
(async()=>{
 await new Promise(r=>setTimeout(r,0));
 const preview=d.getElementById('saved-document-preview');
 assert.equal(preview.querySelector('pre').textContent,'<script>private</script>');
 assert.equal(preview.querySelector('script'),null);
 let disposed=0;preview.disposePreview=()=>disposed++;
 d.querySelector('button').click();await new Promise(r=>setTimeout(r,0));
 assert.equal(disposed,1);assert.match(preview.textContent,/deleted/);
 assert.equal(requests[1][0],'/uploads/owned-id');assert.equal(requests[1][1].headers['X-CSRFToken'],'token');
 assert.equal(requests[1][1].method,'DELETE');
 assert.match(d.getElementById('document-status').textContent,/cleanup is queued/);
 dom.window.close();console.log('Saved document preview escapes text; deletion carries CSRF and disposes preview.');
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
