/* DOM-level coverage for actual inline editing and context-picker interaction. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const dom = new JSDOM(`<meta name="csrf-token" content="test"><div class="chat-page" data-conversation=""><form id="chat-composer">
<textarea id="chat-input"></textarea><button type="button" id="chat-mode" value="wick"></button>
<div id="wick-context-controls"><button type="button" id="wick-add-context">+</button>
<div id="wick-context-menu" hidden><button type="button" data-context-category="pages">Pages</button><button type="button" data-context-category="assets">Assets</button><div id="wick-selected-context" hidden></div></div>
<div id="wick-asset-picker" hidden><button type="button" id="wick-asset-back"></button><button type="button" id="wick-asset-close"></button><strong id="wick-asset-title"></strong>
<input id="wick-asset-query"><div id="wick-asset-kind"><button type="button" data-kind=""></button><button type="button" data-kind="paper"></button></div>
<div id="wick-asset-status"></div><div id="wick-asset-results"></div><button type="button" id="wick-asset-more"></button></div>
<span id="wick-context-status"></span></div><script type="application/json" id="wick-context-data">[{"kind":"page","id":"search","label":"Paper search"}]</script></form></div>`, { url: 'https://alfred.example/chat/assistant', runScripts: 'outside-only' });
const w = dom.window, d = w.document;
const id = '00000000-0000-0000-0000-000000000001';
let requests = [];
w.fetch = async (url) => {
  requests.push(url);
  const category = new URL(url, w.location).searchParams.get('category');
  return { ok: true, json: async () => ({ items: category === 'pages' ? [{kind:'page', id:'notes', label:'Notes'}] : [{kind:'paper', id, label:'Paper title'}], next_cursor: null }) };
};
for (const script of ['wick_mentions.js', 'wick_editor.js', 'wick_context.js']) {
  w.eval(fs.readFileSync(path.join(root, 'dashboard/static/js', script), 'utf8'));
}
const editor = w.WickEditor.element, input = d.getElementById('chat-input');
const tick = () => new Promise(resolve => setTimeout(resolve, 240));
function caret(node, offset) {
  editor.focus();
  const range = d.createRange(); range.setStart(node, offset); range.collapse(true);
  const selection = w.getSelection(); selection.removeAllRanges(); selection.addRange(range);
  d.dispatchEvent(new w.Event('selectionchange'));
}
function type(text) {
  input.value = text; const node = editor.lastChild; caret(node, node.textContent.length);
  editor.dispatchEvent(new w.Event('input', { bubbles: true }));
}
(async () => {
  assert.equal(input.hidden, true);
  assert.equal(editor.dataset.placeholder, 'Type @ to include an asset and # for pages');
  assert.equal(editor.dataset.empty, 'true');
  assert.equal(editor.textContent, ''); assert.equal(input.value, '');
  type('Hello'); assert.equal(editor.textContent, 'Hello'); assert.equal(w.WickContext.references().length, 1);
  assert.equal(w.WickContext.formatPrompt('Hello'), 'Hello');
  d.querySelector('#wick-selected-context button').click();
  type('Review @pap'); await tick();
  assert.equal(editor.dataset.empty, 'false');
  assert.equal(d.getElementById('wick-asset-picker').hidden, false);
  assert.equal(w.getSelection().anchorNode.parentElement, editor);
  assert(requests.some(url => url.includes('category=assets') && url.includes('q=pap')));
  editor.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  assert.equal(editor.querySelector('a').textContent, '@Paper title');
  assert.equal(editor.dataset.empty, 'false');
  assert.equal(editor.textContent, 'Review @Paper title ');
  assert.equal(input.value, 'Review [@Paper title](/paper/' + id + ') ');
  assert.equal(editor.querySelector('a').getAttribute('contenteditable'), 'false');
  assert.deepEqual(JSON.parse(JSON.stringify(w.WickContext.references())), [{kind:'paper', id}]);
  // Removing the atomic link updates selected context and keeps it removed after clearing.
  editor.querySelector('a').remove(); editor.dispatchEvent(new w.Event('input', { bubbles:true }));
  assert.equal(w.WickContext.references().length, 0);
  input.value = ''; assert.equal(editor.querySelector('a'), null);
  type('Open #not'); await tick();
  assert(requests.some(url => url.includes('category=pages') && url.includes('q=not')));
  d.querySelector('.wick-asset-result').click();
  assert.equal(editor.textContent, 'Open #Notes ');
  assert.equal(editor.querySelector('a').getAttribute('href'), '/notes');
  input.value = input.value.replace('Open ', ''); assert.equal(editor.dataset.empty, 'false');
  assert(!input.value.includes('Type @'));
  // Clearing a draft keeps background context without inserting tokens into the next draft.
  input.value = ''; assert.equal(editor.querySelector('a'), null); assert.equal(w.WickContext.references().length, 1);
  assert.equal(editor.dataset.empty, 'true');
  const mode = d.getElementById('chat-mode'); mode.value = 'research'; mode.dispatchEvent(new w.Event('change'));
  assert.equal(editor.hidden, true); assert.equal(input.hidden, false); assert.equal(input.value.trim(), '');
  input.value = 'Research draft'; assert.equal(input.value, 'Research draft');
  mode.value = 'wick'; mode.dispatchEvent(new w.Event('change')); assert.equal(editor.hidden, false);
  input.disabled = true; await tick(); assert.equal(editor.getAttribute('aria-disabled'), 'true');
  input.disabled = false; await tick(); assert.equal(editor.getAttribute('aria-disabled'), 'false');
  // Local pasted HTML stays literal text and cannot create executable DOM.
  input.value = ''; d.querySelector('#wick-selected-context button').click(); input.value = '';
  caret(editor, 0);
  const paste = new w.Event('paste', { bubbles:true, cancelable:true });
  Object.defineProperty(paste, 'clipboardData', { value:{ getData:()=>'<img src=x onerror=alert(1)>' } });
  editor.dispatchEvent(paste); assert.equal(editor.querySelector('img'), null); assert(input.value.includes('<img'));
  // Emulate browser focus moving the selection to offset zero, unlike jsdom's default.
  const realFocus = editor.focus.bind(editor);
  editor.focus = () => { realFocus(); const range = d.createRange(); range.selectNodeContents(editor); range.collapse(true); const selection = w.getSelection(); selection.removeAllRanges(); selection.addRange(range); };
  // Insert via the plus picker in the middle of a draft, retaining surrounding text.
  input.value = 'Compare this with that'; caret(editor.firstChild, 13);
  d.getElementById('wick-add-context').click(); d.querySelector('[data-context-category="assets"]').click(); await tick();
  d.querySelector('.wick-asset-result').click();
  assert.equal(editor.textContent, 'Compare this @Paper title with that');
  // Round-trip serialized draft links without making raw HTML executable.
  const draft = input.value; input.value = draft;
  assert.equal(editor.querySelectorAll('a').length, 1);
  assert.equal(w.WickMentions.fromHref('/notes#note-' + id, 'Note').id, id);
  assert.equal(w.WickMentions.fromHref('/goals#goal-' + id, 'Goal').id, id);
  for (const offset of [0, 7, 17]) {
    input.value = 'Beginning and end'; caret(editor.firstChild, offset);
    d.getElementById('wick-add-context').click(); d.querySelector('[data-context-category="assets"]').click(); await tick();
    d.querySelector('.wick-asset-result').click();
    assert.equal(input.value, 'Beginning and end'.slice(0, offset) + '[@Paper title](/paper/' + id + ') ' + 'Beginning and end'.slice(offset));
  }
  editor.querySelector('a').remove(); editor.dispatchEvent(new w.Event('input', {bubbles:true}));
  editor.innerHTML = 'first<div>second</div><div><br></div>'; editor.dispatchEvent(new w.Event('input', {bubbles:true}));
  assert.equal(input.value, 'first\nsecond\n');
  type('email@example.com'); await tick(); assert.equal(d.getElementById('wick-asset-picker').hidden, true);
  type('@'); editor.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
  assert.equal(d.getElementById('wick-asset-picker').hidden, true);
  let submitted = 0; d.getElementById('chat-composer').requestSubmit = () => { submitted++; };
  editor.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Enter', shiftKey:true, bubbles:true, cancelable:true}));
  assert.equal(submitted, 0);
  editor.dispatchEvent(new w.Event('compositionstart'));
  editor.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true})); assert.equal(submitted, 0);
  editor.dispatchEvent(new w.Event('compositionend'));
  editor.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
  editor.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true})); assert.equal(submitted, 1);
  dom.window.close();
})().catch(error => { console.error(error); dom.window.close(); process.exitCode = 1; });
