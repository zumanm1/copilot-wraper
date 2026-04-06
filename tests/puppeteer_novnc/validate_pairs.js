const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  page.setDefaultTimeout(600000);

  console.log('1. Navigating to http://localhost:6090/pairs ...');
  await page.goto('http://localhost:6090/pairs', { waitUntil: 'networkidle2' });
  
  const title = await page.title();
  console.log('   Page title:', title);

  // Verify agent rows are present
  const agentRows = await page.$$eval('tr[data-agent-id]', rows => rows.map(r => r.getAttribute('data-agent-id')));
  console.log('2. Agent rows found:', agentRows);

  // Check initial status badges
  const badges = await page.$$eval('[id^="badge-"]', els => els.map(el => ({ id: el.id, text: el.textContent.trim() })));
  console.log('3. Initial badges:', JSON.stringify(badges));

  // Click "Run All Parallel" button
  console.log('4. Clicking Run All Parallel...');
  const runAllBtn = await page.$('button#run-all-btn, button[onclick*="runAll"], button');
  
  // Find the correct run-all button by text
  const clicked = await page.evaluate(() => {
    const btns = [...document.querySelectorAll('button')];
    const btn = btns.find(b => b.textContent.includes('Run All Parallel') || b.textContent.includes('Run All'));
    if (btn) { btn.click(); return btn.textContent.trim(); }
    return null;
  });
  console.log('   Clicked:', clicked);

  if (!clicked) {
    console.error('ERROR: Run All button not found!');
    await browser.close();
    process.exit(1);
  }

  // Wait for all agents to complete (watch for all badges to change from pending)
  console.log('5. Waiting for all agents to complete (up to 10min)...');
  await page.waitForFunction(() => {
    const badges = [...document.querySelectorAll('[id^="badge-"]')];
    if (badges.length === 0) return false;
    // All badges should not be "..." or "running" or empty
    return badges.every(b => {
      const t = b.textContent.trim().toLowerCase();
      return t !== '...' && t !== 'running' && t !== '' && t !== '—';
    });
  }, { timeout: 600000, polling: 3000 });

  // Collect final results
  const results = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('tr[data-agent-id]')];
    return rows.map(row => {
      const agentId = row.getAttribute('data-agent-id');
      const badge = row.querySelector('[id^="badge-"]');
      const elapsed = row.querySelector('[id^="elapsed-"]');
      const resp = row.querySelector('[id^="resp-"]');
      return {
        agent: agentId,
        badge: badge ? badge.textContent.trim() : '?',
        elapsed: elapsed ? elapsed.textContent.trim() : '?',
        resp: resp ? resp.textContent.trim().slice(0, 80) : '?'
      };
    });
  });

  console.log('\n=== FINAL RESULTS ===');
  let passed = 0, failed = 0;
  for (const r of results) {
    const ok = r.badge.toLowerCase().includes('pass') || r.badge.toLowerCase().includes('ok') || r.badge === '✓';
    if (ok) passed++; else failed++;
    console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${r.agent} | badge="${r.badge}" elapsed="${r.elapsed}" resp="${r.resp}"`);
  }
  console.log(`\nSUMMARY: ${passed}/${results.length} passed, ${failed} failed`);

  // Screenshot
  await page.screenshot({ path: '/tmp/pairs_result.png', fullPage: true });
  console.log('Screenshot saved to /tmp/pairs_result.png');

  await browser.close();
  process.exit(failed > 0 ? 1 : 0);
})();
