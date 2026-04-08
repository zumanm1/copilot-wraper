/**
 * debug_quick_prompt.mjs — one-shot diagnostic
 * Clicks the first quick-prompt button, waits 45s, dumps the DOM.
 */
import puppeteer from 'puppeteer';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const browser = await puppeteer.launch({
  headless: true,
  executablePath: CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});

const page = await browser.newPage();
page.setDefaultTimeout(120000);

// Track /api/chat network traffic
const netLog = [];
page.on('request',  r => { if (r.url().includes('/api/chat')) netLog.push('REQ  ' + r.method() + ' ' + r.url()); });
page.on('response', r => { if (r.url().includes('/api/chat')) netLog.push('RESP ' + r.status() + ' ' + r.url()); });

// Capture console from page
page.on('console', msg => {
  if (msg.type() === 'error') console.log('[PAGE ERROR]', msg.text());
});
page.on('pageerror', err => console.log('[PAGE EXCEPTION]', err.message));

console.log('→ navigating...');
await page.goto('http://localhost:6090/chat', { waitUntil: 'networkidle2' });
console.log('→ page loaded');

const btns = await page.$$('.quick[data-prompt]');
console.log('→ buttons found:', btns.length);

const promptText = await btns[0].evaluate(el => el.dataset.prompt);
console.log('→ clicking:', promptText);
await btns[0].click();

// Poll DOM every 5s for 60s
for (let i = 0; i < 12; i++) {
  await new Promise(r => setTimeout(r, 5000));
  const snap = await page.evaluate(() => {
    const bubbles = Array.from(document.querySelectorAll('.bubble'));
    return bubbles.map(b => ({
      cls: b.className,
      len: b.textContent.trim().length,
      txt: b.textContent.trim().slice(0, 100),
    }));
  });
  console.log(`\n[t+${(i+1)*5}s] bubbles (${snap.length}):`);
  snap.forEach(b => console.log('  ', b.cls, '| len:', b.len, '|', b.txt.slice(0,60)));

  // Early exit if non-empty assistant bubble appears
  const found = snap.find(b => b.cls.includes('assistant') && b.len > 5 && !b.txt.includes('…') && !b.txt.includes('typing'));
  if (found) {
    console.log('\n✓ GOT RESPONSE BUBBLE:', found.txt.slice(0, 120));
    break;
  }
}

console.log('\nNetwork log:');
netLog.forEach(l => console.log(' ', l));

await page.screenshot({ path: '/tmp/debug_quick_prompt.png', fullPage: true });
console.log('→ screenshot saved to /tmp/debug_quick_prompt.png');

await browser.close();
