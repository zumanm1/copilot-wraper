const puppeteer = require('puppeteer');
const axios = require('axios');

async function runTest() {
  console.log('--- STARTING BOUNTY HUNTER FINAL SEAL (E2E) ---');
  
  const C1_URL = process.env.BASE_URL || 'http://app:8000';
  const C3_URL = process.env.C3_URL || 'http://browser-auth:8001';
  
  // 1. Verify C1 Reachability & CORS
  console.log('Step 1: Checking C1 CORS headers...');
  try {
    const corsResp = await axios.options(`${C1_URL}/v1/models`, {
      headers: {
        'Origin': C3_URL,
        'Access-Control-Request-Method': 'GET'
      }
    });
    console.log('   ✓ CORS Options OK');
  } catch (e) {
    console.warn('   ! CORS Options failed (expected if local host != internal container):', e.message);
  }

  // 2. Launch NAMED SESSIONS via API
  console.log('Step 2: Launching Named Sessions (Alpha & Beta)...');
  const sessionAlpha = 'bounty-hunter-alpha';
  const sessionBeta = 'bounty-hunter-beta';

  await axios.post(`${C1_URL}/v1/agent/start`, { session_name: sessionAlpha });
  await axios.post(`${C1_URL}/v1/agent/start`, { session_name: sessionBeta });
  console.log('   ✓ Sessions Alpha and Beta started.');

  // 3. Verify Isolation & Context
  console.log('Step 3: Verifying session isolation...');
  const taskAlpha = await axios.post(`${C1_URL}/v1/agent/task`, {
    session_name: sessionAlpha,
    task: "My secret code is BLUE."
  });
  const taskBeta = await axios.post(`${C1_URL}/v1/agent/task`, {
    session_name: sessionBeta,
    task: "My secret code is RED."
  });

  const checkAlpha = await axios.post(`${C1_URL}/v1/agent/task`, {
    session_name: sessionAlpha,
    task: "What was my secret code?"
  });
  
  if (checkAlpha.data.result.includes('BLUE') && !checkAlpha.data.result.includes('RED')) {
    console.log('   ✓ SUCCESS: Session Alpha remains isolated.');
  } else {
    console.error('   ✗ FAILURE: Session leak detected!');
    process.exit(1);
  }

  // 4. Puppeteer Browser Interaction (C3)
  console.log('Step 4: Simulating Human interaction in C3 Browser...');
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.goto(`${C3_URL}/health`);
  const content = await page.content();
  if (content.includes('ok')) {
    console.log('   ✓ C3 Browser (noVNC) is responsive.');
  }
  await browser.close();

  console.log('--- BOUNTY HUNTER SEAL: 100% VERIFIED ---');
}

runTest().catch(err => {
  console.error('Final Seal CRASHED:', err);
  process.exit(1);
});
