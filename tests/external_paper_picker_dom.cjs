/* Verify real picker code imports only selected candidates and retries membership. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM(`<button class="js-add-papers"></button><dialog id="paper-picker" data-search-url="/local" data-external-search-url="/external" data-import-url="/import" data-add-url="/add">
<form id="picker-search"><input name="q"><select name="scope"><option value="corpus">Corpus</option><option value="external">External</option></select><button>Search</button></form>
<div id="picker-results"></div><p id="picker-status"></p><button id="picker-add"></button><button id="picker-more"></button><button id="picker-close"></button><span id="picker-selected"></span></dialog>`, {url:'https://alfred.example',runScripts:'outside-only'});
const w=dom.window,d=w.document,dialog=d.getElementById('paper-picker');
dialog.showModal=()=>{}; dialog.close=()=>{};
let fetches=[],writes=[],fail=true;
w.fetch=async url=>{fetches.push(url);return {ok:true,json:async()=>url.startsWith('/local')?{items:[],next_cursor:null}:{items:[{openalex_id:'W123',title:'Selected',url:'https://openalex.org/W123'},{openalex_id:'W999',title:'Not selected',url:'https://openalex.org/W999'}],next_page:2}};};
w.RC={toast:()=>{},postJSON:async(url,data)=>{writes.push([url,data]);if(url==='/import')return {paper_id:'local-id'};if(fail)throw new Error('Membership unavailable');return {status:'ok'};}};
w.eval(fs.readFileSync(path.resolve(__dirname,'../dashboard/static/js/collections.js'),'utf8'));
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{
 d.querySelector('.js-add-papers').click(); await settle();
 const form=d.getElementById('picker-search'); form.elements.scope.value='external';
 form.dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
 assert.equal(fetches.length,1); // Blank external search must not call provider.
 form.elements.q.value='Evidence';form.dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
 assert.ok(fetches[1].startsWith('/external?'));assert.equal(writes.length,0);
 const check=d.querySelector('#picker-results input');check.checked=true;check.dispatchEvent(new w.Event('change'));
 d.getElementById('picker-add').click();await settle();
 assert.deepEqual(JSON.parse(JSON.stringify(writes)),[['/import',{openalex_id:'W123'}],['/add',{paper_id:'local-id'}]]);
 assert.match(d.getElementById('picker-status').textContent,/Remaining selections were kept/);
 assert.equal(d.getElementById('picker-selected').textContent,'1 selected');
 fail=false;d.getElementById('picker-add').click();await settle();
 assert.equal(writes.filter(([url])=>url==='/import').length,1);
 assert.equal(writes.filter(([url])=>url==='/add').length,2);
 assert.equal(d.getElementById('picker-selected').textContent,'0 selected');
 console.log('External picker DOM: selected-only import and membership retry passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
