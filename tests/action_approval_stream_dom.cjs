/* Real chat UI: streamed approval progress must not refresh an unfinished task. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM(`<meta name="csrf-token" content="token"><div class="chat-page" data-owner="owner" data-conversation="cid" data-surface="assistant" data-chat-mode="wick"><div class="chat-stage"><div id="chat-thread"></div><form id="chat-composer"><textarea id="chat-input"></textarea><button class="composer-send"></button><button type="button" class="composer-stop"></button></form></div></div>`, {url:'https://alfred.example/chat/assistant', runScripts:'outside-only'});
const w = dom.window, d = w.document, messages = [];
w.HTMLElement.prototype.scrollIntoView = function() {};
w.TextDecoder = TextDecoder;
Object.defineProperty(w,'parent',{value:{postMessage: message => messages.push(message)}});
w.sessionStorage.setItem('alfred-active-run:assistant:owner','run');
const proposal = id => ({run_id:'run',approval_id:id,tool:'create_collection',label:'Create a collection',target_label:'Workspace',display:[]});
let approvals = 0, release;
const frame = event => 'data: '+JSON.stringify(event)+'\n\n';
w.fetch = async (url, options) => {
  if (url === '/chat/runs/run') return {ok:true,json:async()=>({state:'awaiting_approval'})};
  if (url === '/chat/runs/run/approval') return {ok:true,json:async()=>({approval:proposal('first')})};
  if (url.startsWith('/chat/approvals/')) {
    approvals++;
    assert.equal(options.headers.Accept,'text/event-stream');
    assert.equal(JSON.parse(options.body).decision,approvals === 1 ? 'always' : 'once');
    let step = 0;
    const first = approvals === 1;
    return {ok:true,headers:{get:()=> 'text/event-stream'},body:{getReader:()=>({read:async()=>{
      step++;
      if (step === 1) return {value:new TextEncoder().encode(frame({type:'run',run_id:'run'})+frame({type:'tool_start',name:'create_collection'})+frame({type:'tool_end',name:'create_collection',ok:true})),done:false};
      if (step === 2) {
        await new Promise(resolve => release = resolve);
        return {value:new TextEncoder().encode(frame({type:'done',result:first
          ? {status:'awaiting_approval',approval:proposal('second'),conversation_id:'cid'}
          : {status:'ok',answer:'Completed.',conversation_id:'cid'}})),done:false};
      }
      return {done:true};
    }})}};
  }
  throw new Error('Unexpected '+url);
};
w.eval(fs.readFileSync(path.join(__dirname,'../dashboard/static/js/chat.js'),'utf8'));
const tick = () => new Promise(resolve => setTimeout(resolve,30));
(async()=>{
  await tick();
  [...d.querySelector('.chat-action-approval').querySelectorAll('button')].find(b=>b.textContent==='Always allow').click();
  await tick();
  assert.equal(d.getElementById('chat-input').disabled,true);
  assert(d.getElementById('chat-thread').textContent.includes('collection'));
  assert.equal(messages.filter(m=>m.type==='writes').length,0);
  release(); await tick();
  assert(d.querySelector('.chat-action-approval'));
  assert.equal(messages.filter(m=>m.type==='writes').length,0);
  assert.equal(w.sessionStorage.getItem('alfred-active-run:assistant:owner'),'run');
  [...d.querySelector('.chat-action-approval').querySelectorAll('button')].find(b=>b.textContent==='Allow once').click();
  await tick(); release(); await tick();
  assert.equal(d.querySelector('.chat-action-approval'),null);
  assert.equal(d.getElementById('chat-input').disabled,false);
  assert.equal(w.sessionStorage.getItem('alfred-active-run:assistant:owner'),null);
  assert.equal(messages.filter(m=>m.type==='writes').length,1);
  assert.equal(messages.filter(m=>m.type==='writes')[0].value.length,2);
  console.log('Streamed approval continuation DOM passed'); w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1;});
