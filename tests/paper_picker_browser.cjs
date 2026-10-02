/* Optional real-browser check, driven by test_collection_paper_picker.py. */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES + '/playwright');
(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.PICKER_TEST_URL);
    await page.locator('.js-add-papers').first().click();
    await page.locator('#picker-results input').first().waitFor();
    assert.equal(await page.locator('#picker-results input:disabled').count(), 1);
    assert.equal(await page.locator('#picker-results script').count(), 0);
    const selectable = page.locator('#picker-results input:not(:disabled)');
    await selectable.nth(0).check(); await selectable.nth(1).check();
    assert.equal(await page.locator('#picker-selected').textContent(), '2 selected');
    // A partial failure must retain the uncommitted selection for retry.
    let calls = 0;
    await page.route('**/papers', async route => {
      if (route.request().method() === 'POST' && ++calls === 2) {
        await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Temporary failure'})});
      } else await route.continue();
    });
    await page.locator('#picker-add').click();
    await page.waitForFunction(() => document.getElementById('picker-status').textContent.includes('Remaining selections'));
    assert.equal(await page.locator('#picker-selected').textContent(), '1 selected');
    await page.screenshot({ path: '/tmp/alfred-paper-picker.png' });
    await page.locator('#picker-add').click();
    await page.waitForFunction(() => !document.getElementById('paper-picker').open);
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('.reading-item').count(), 3);
    assert.deepEqual(errors, []);
    console.log('Picker: duplicate badge, safe titles, multi-select, partial failure, retry and refresh passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
