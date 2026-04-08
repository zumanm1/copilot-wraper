/**
 * validate_quick_prompts.mjs
 * ──────────────────────────
 * Puppeteer E2E regression test for the quick-prompt buttons on /chat.
 *
 * Verifies:
 *   1. Page loads and all 4 quick-prompt buttons are present.
 *   2. Clicking each button produces a non-empty assistant response bubble.
 *   3. No "error" SSE event surfaces in the UI (no error bubble rendered).
 *   4. The streaming loop completes (typing indicator disappears).
 *
 * Root bugs validated (must NOT regress):
 *   BUG-1: asyncio.wait_for cancels httpx iterator → empty response after 15s
 *   BUG-2: aiohttp aiodns fails in Docker → C1 never reaches C3
 *
 * Usage:
 *   cd tests/puppeteer_novnc
 *   npm install          # first time only
 *   node validate_quick_prompts.mjs
 *
 * Exit code 0 = all assertions passed.
 * Exit code 1 = one or more assertions failed.
 */

import puppeteer from 'puppeteer';

const BASE_URL   = process.env.BASE_URL   || 'http://localhost:6090';
const TIMEOUT_MS = parseInt(process.env.TIMEOUT_MS || '150000', 10); // 2.5 min per prompt (accounts for C3 pool wait)

let passed = 0;
let failed = 0;

function pass(msg) { console.log(`  ✓ ${msg}`); passed++; }
function fail(msg) { console.error(`  ✗ ${msg}`); failed++; }

const CHROME_PATH = process.env.CHROME_PATH
  || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const browser = await puppeteer.launch({
  headless: true,
  executablePath: CHROME_PATH,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
  protocolTimeout: TIMEOUT_MS * 2,
});

try {
  const page = await browser.newPage();
  page.setDefaultTimeout(TIMEOUT_MS);

  /* ── Intercept console errors so we can surface JS exceptions ── */
  const jsErrors = [];
  page.on('pageerror', err => jsErrors.push(err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') jsErrors.push(msg.text());
  });

  /* ── 1. Navigate to /chat ── */
  console.log(`\n[1] Navigating to ${BASE_URL}/chat …`);
  const resp = await page.goto(`${BASE_URL}/chat`, { waitUntil: 'networkidle2', timeout: 30000 });
  if (resp && resp.status() === 200) {
    pass('Page loaded with HTTP 200');
  } else {
    fail(`Page load failed: HTTP ${resp?.status()}`);
    process.exit(1);
  }

  /* ── 2. Verify quick-prompt buttons exist ── */
  console.log('\n[2] Checking quick-prompt buttons …');
  const buttons = await page.$$('.quick[data-prompt]');
  if (buttons.length >= 4) {
    pass(`Found ${buttons.length} quick-prompt buttons`);
  } else {
    fail(`Expected ≥ 4 quick-prompt buttons, found ${buttons.length}`);
  }

  /* Collect button prompts for labelling */
  const buttonPrompts = await page.$$eval('.quick[data-prompt]', els =>
    els.map(el => el.dataset.prompt)
  );
  console.log(`   Prompts: ${buttonPrompts.join(' | ')}`);

  /* ── 3. Click each button and verify response ── */
  for (let i = 0; i < buttonPrompts.length; i++) {
    const prompt = buttonPrompts[i];
    console.log(`\n[3.${i + 1}] Clicking "${prompt}" …`);

    /* Clear previous chat so UI is fully reset */
    const clearBtn = await page.$('#clear-chat');
    if (clearBtn) {
      await clearBtn.click();
      /* Wait until any streaming-bubble is gone and typing indicator is hidden */
      await page.waitForFunction(
        () => !document.querySelector('.typing-indicator') &&
              !document.querySelector('.streaming-bubble'),
        { timeout: 10000 }
      ).catch(() => {});
      await new Promise(r => setTimeout(r, 600));
    }

    /* Screenshot before */
    await page.screenshot({ path: `/tmp/quick_prompt_${i}_before.png` });

    /* Click the quick-prompt button — re-query after clear */
    const btns = await page.$$('.quick[data-prompt]');
    await btns[i].click();

    /* Wait for a non-empty assistant bubble — covers both fast and slow responses.
     * This is race-condition-free: we don't depend on catching the transient
     * typing indicator; we simply wait until actual text has been rendered. */
    let bubbleText = '';
    try {
      await page.waitForFunction(
        () => {
          const bodies = document.querySelectorAll('.bubble.assistant .bubble-body');
          const last = bodies[bodies.length - 1];
          return last && last.textContent.trim().length > 5
            && !last.querySelector('.typing-dots');  // not still loading
        },
        { timeout: TIMEOUT_MS }
      );
      bubbleText = await page.$eval(
        '.bubble.assistant:last-of-type .bubble-body',
        el => el.textContent.trim()
      ).catch(() => '');
      pass(`Response received (${bubbleText.length} chars) for "${prompt}"`);
      pass(`Stream completed for "${prompt}"`);
    } catch {
      fail(`No response bubble appeared within ${TIMEOUT_MS/1000}s for "${prompt}"`);
      fail(`Stream timed out for "${prompt}"`);
      await page.screenshot({ path: `/tmp/quick_prompt_${i}_timeout.png` });
      continue;
    }

    /* Assert: no error bubble rendered */
    const errorBubbles = await page.$$eval('.bubble.error, .bubble-error, [data-type="error"]', els =>
      els.map(el => el.textContent.trim())
    );
    if (errorBubbles.length === 0) {
      pass(`No error bubbles for "${prompt}"`);
    } else {
      fail(`Error bubble(s) found for "${prompt}": ${errorBubbles.join('; ')}`);
    }

    /* Assert: no "C10 sandbox unavailable" text in page (the specific regression) */
    const pageText = await page.evaluate(() => document.body.innerText);
    if (!pageText.includes('C10 sandbox unavailable')) {
      pass(`No "C10 sandbox unavailable" regression for "${prompt}"`);
    } else {
      fail(`BUG REGRESSION: "C10 sandbox unavailable" found for "${prompt}"`);
    }

    /* Screenshot after */
    await page.screenshot({ path: `/tmp/quick_prompt_${i}_after.png` });

    /* Brief cooldown so the C3 pool tab recycles before the next prompt */
    if (i < buttonPrompts.length - 1) await new Promise(r => setTimeout(r, 5000));
  }

  /* ── 4. Assert no JS errors ── */
  console.log('\n[4] Checking for JS errors …');
  const relevantErrors = jsErrors.filter(e =>
    !e.includes('favicon') &&
    !e.includes('net::ERR_ABORTED') &&
    !e.includes('sendMessage') &&        // browser extension noise
    !e.includes('404') &&               // missing static assets unrelated to chat
    !e.includes('503')                  // background health/runtime pollers unavailable
  );
  if (relevantErrors.length === 0) {
    pass('No JS errors on page');
  } else {
    fail(`JS errors detected: ${relevantErrors.join('; ')}`);
  }

} finally {
  await browser.close();
}

/* ── Summary ── */
console.log('\n' + '═'.repeat(55));
console.log(`  Quick Prompt E2E: ${passed} passed, ${failed} failed`);
console.log('═'.repeat(55));

if (failed > 0) {
  console.error('\nFAILED — one or more quick-prompt assertions did not pass.');
  process.exit(1);
} else {
  console.log('\nPASSED — all quick-prompt assertions verified end-to-end.');
  process.exit(0);
}
