/* Exercise the real recovery UI, literal rendering and single-click decision path. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM(`<meta name="csrf-token" content="token"><div class="chat-page" data-owner="owner" data-conversation="cid"><div class="chat-stage"><div id="chat-thread"></div><form id="chat-composer"><textarea id="chat-input"></textarea><div id="wick-permission-picker"><button type="button" id="wick-permission" value="ask"></button><div class="mode-pick-menu" hidden><button type="button" role="option" data-permission="ask">Ask</button><button type="button" role="option" data-permission="autonomous">Autonomous</button></div></div><button class="composer-send"></button><button type="button" class="composer-stop"></button></form></div></div>`, {url:'https://alfred.example/chat', runScripts:'outside-only'});
const w = dom.window, d = w.document;
w.HTMLElement.prototype.scrollIntoView = function() {};
w.sessionStorage.setItem('alfred-active-run', 'run');
let decisions = 0, release;
w.fetch = async (url, options) => {
  if (url === '/chat/runs/run') return {ok:true,json:async()=>({state:'awaiting_approval'})};
  if (url === '/chat/runs/run/approval') return {ok:true,json:async()=>({approval:{run_id:'run',approval_id:'approval',tool:'create_note',label:'Create a note',target_label:'your workspace',display:[{label:'Content',value:'<img src=x onerror=alert(1)>'}]}})};
  if (url === '/chat/approvals/approval') {
    decisions++;
    assert.equal(options.headers['X-CSRFToken'], 'token');
    assert.deepEqual(JSON.parse(options.body), {decision:'deny'});
    await new Promise(resolve => release = resolve);
    return {ok:true,json:async()=>({status:'stopped',message:'Declined',tool_calls:[]})};
  }
  throw new Error('Unexpected '+url);
};
w.eval(fs.readFileSync(path.join(__dirname,'../dashboard/static/js/chat.js'),'utf8'));
const tick = () => new Promise(resolve => setTimeout(resolve,30));
(async()=>{
  await tick();
  const card = d.querySelector('.chat-action-approval');
  assert(card); assert.equal(card.querySelector('img'),null);
  assert.equal(d.getElementById('wick-permission').disabled,true);
  assert(card.textContent.includes('<img'));
  const decline = [...card.querySelectorAll('button')].find(b=>b.textContent==='Decline');
  decline.click(); decline.click(); await tick();
  assert.equal(decisions,1); assert.equal(d.getElementById('chat-input').disabled,true);
  release(); await tick();
  assert.equal(d.querySelector('.chat-action-approval'),null);
  assert.equal(d.getElementById('chat-input').disabled,false);
  assert.equal(w.sessionStorage.getItem('alfred-active-run'),null);
  const permission = d.getElementById('wick-permission');
  assert.equal(permission.disabled,false); assert.equal(permission.value,'ask');
  permission.click(); assert.equal(permission.getAttribute('aria-expanded'),'true');
  d.querySelector('[data-permission="autonomous"]').click();
  assert.equal(permission.value,'autonomous'); assert(permission.textContent.includes('Autonomous'));
  permission.click(); d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));
  assert.equal(permission.getAttribute('aria-expanded'),'false');
  permission.click(); d.querySelector('[data-permission="ask"]').click();
  assert.equal(permission.value,'ask');
  console.log('Action approval DOM passed'); w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1;});
