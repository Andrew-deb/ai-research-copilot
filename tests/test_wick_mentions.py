"""Execute reference-formatting JS to check deep links and safe user rendering."""
import subprocess
from pathlib import Path


def test_internal_mentions_format_and_render_without_html_or_external_links():
    script = Path(__file__).resolve().parents[1] / 'dashboard/static/js/wick_mentions.js'
    code = r'''
const assert = require('node:assert/strict');
global.window = {};
global.document = {
  createTextNode(text) { return { type: 'text', text }; },
  createElement(type) { return { type }; }
};
require(process.argv[1]);
const m = window.WickMentions;
const id = '00000000-0000-0000-0000-000000000001';
assert.equal(m.href({kind:'paper', id}), '/paper/' + id);
assert.equal(m.href({kind:'note', id}), '/notes#note-' + id);
assert.equal(m.href({kind:'goal', id}), '/goals#goal-' + id);
assert.equal(m.href({kind:'page', id:'notes'}), '/notes');
assert.equal(m.href({kind:'page', id:'//evil.example'}), null);
assert.equal(m.href({kind:'paper', id:'javascript:alert(1)'}), null);
assert.equal(m.href({kind:'paper', id, available:false}), null);
const item = {kind:'paper', id, label:'Research paper'};
const token = m.token(item);
assert.equal(m.format('Compare this', [item]), 'Compare this\n\n' + token);
assert.equal(m.format('Compare ' + token, [item]), 'Compare ' + token);
const container = {nodes:[], appendChild(node) { this.nodes.push(node); }};
m.render(container, '<script>alert(1)</script> ' + token + ' [@bad](https://evil.example) [@bad](javascript:alert(1))');
const links = container.nodes.filter(n => n.type === 'a');
assert.equal(links.length, 1);
assert.equal(links[0].href, '/paper/' + id);
assert.equal(links[0].textContent, '@Research paper');
assert.equal(links[0].rel, 'noopener');
assert(container.nodes.some(n => n.type === 'text' && n.text.includes('<script>')));
assert(container.nodes.some(n => n.type === 'text' && n.text.includes('https://evil.example')));
'''
    subprocess.run(['node', '-e', code, str(script)], check=True, capture_output=True, text=True)
