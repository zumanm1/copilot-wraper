import puppeteer from 'puppeteer';

const browser = await puppeteer.launch({
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
  protocolTimeout: 600000,
});
const page = await browser.newPage();
page.setDefaultTimeout(600000);

console.log('1. Navigating to http://localhost:6090/pairs ...');
await page.goto('http://localhost:6090/pairs', { waitUntil: 'networkidle2' });

const title = await page.title();
console.log('   Page title:', title);

// Verify agent rows present — correct selector: rows have id="row-{agentId}"
const agentIds = ['c2-aider', 'c5-claude-code', 'c6-kilocode', 'c7-openclaw', 'c8-hermes', 'c9-jokes'];
const rowChecks = await page.$$eval('tbody#results-body tr', rows => rows.map(r => r.id));
console.log('2. Table rows found:', rowChecks);

if (rowChecks.length === 0) {
  await page.screenshot({ path: '/tmp/pairs_debug.png', fullPage: true });
  console.error('ERROR: No rows in results table!');
  await browser.close();
  process.exit(1);
}

// Click Run All Parallel button (id="run-parallel")
console.log('3. Clicking #run-parallel button...');
await page.click('#run-parallel');
console.log('   Clicked Run All Parallel ⚡');

// Poll the C9 /api/validate-runs endpoint to detect completion
// The page JS calls /api/validate which is async — poll every 5s for up to 10min
console.log('4. Polling for results (up to 10min)...');
const { default: https } = await import('http');

const AGENTS = agentIds;
const MAX_WAIT_MS = 600000;
const POLL_MS = 5000;
const t0 = Date.now();

// Use page DOM polling — check status-{id} cells for PASS/FAIL
// Avoid waitForFunction (CDP timeout on long polls); use manual sleep loop instead
let allDone = false;
let finalResults = [];

while (Date.now() - t0 < MAX_WAIT_MS) {
  await new Promise(r => setTimeout(r, POLL_MS));
  const elapsed = Math.round((Date.now() - t0) / 1000);

  // Read each agent's status cell
  const statuses = await page.evaluate((ids) => {
    return ids.map(id => ({
      id,
      badge: (document.getElementById('status-' + id) || {}).textContent || '',
      time:  (document.getElementById('time-'   + id) || {}).textContent || '',
      resp:  (document.getElementById('resp-'   + id) || {}).textContent || '',
    }));
  }, AGENTS);

  const done = statuses.filter(s => {
    const t = s.badge.toLowerCase();
    return t.includes('pass') || t.includes('fail');
  });
  console.log(`   [${elapsed}s] ${done.length}/${AGENTS.length} complete: ${statuses.map(s=>s.badge.trim().slice(0,8)).join(' | ')}`);

  if (done.length === AGENTS.length) {
    allDone = true;
    finalResults = statuses;
    break;
  }
}

if (!allDone) {
  console.error('TIMEOUT: Not all agents completed within 10 minutes.');
  await page.screenshot({ path: '/tmp/pairs_timeout.png', fullPage: true });
  await browser.close();
  process.exit(1);
}

console.log('\n=== FINAL RESULTS ===');
let passed = 0, failed = 0;
for (const r of finalResults) {
  const t = r.badge.toLowerCase();
  const ok = t.includes('pass');
  const fail = t.includes('fail');
  if (ok) passed++; else if (fail) failed++;
  console.log(`  [${ok ? 'PASS' : fail ? 'FAIL' : '????'}] ${r.id} | status="${r.badge.trim()}" time="${r.time.trim()}" resp="${r.resp.trim().slice(0,80)}"`);
}

try { await page.screenshot({ path: '/tmp/pairs_result.png' }); } catch(e) { console.log('Screenshot skipped:', e.message.slice(0,60)); }
console.log('\nScreenshot saved to /tmp/pairs_result.png');
console.log(`SUMMARY: ${passed}/${AGENTS.length} passed, ${failed} failed`);

await browser.close();
process.exit(failed > 0 ? 1 : 0);
