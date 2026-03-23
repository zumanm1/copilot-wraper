/**
 * Validates noVNC reaches "Connected" via root URL and via vnc_auto.html.
 * After connect, checks VNC canvas is not uniformly black (warm Chromium to /setup)
 * and saves a screenshot under tests/reports/.
 *
 * Usage: NOVNC_URL=http://127.0.0.1:6080 node validate_novnc.mjs
 * Requires: docker compose up -d browser-auth
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const base = (process.env.NOVNC_URL || "http://127.0.0.1:6080").replace(/\/$/, "");
const c3Api = (process.env.BROWSER_AUTH_URL || "http://127.0.0.1:8001").replace(
  /\/$/,
  ""
);

const RETRIES = Math.max(1, parseInt(process.env.NOVNC_E2E_RETRIES || "4", 10));
const RETRY_DELAY_MS = parseInt(process.env.NOVNC_E2E_RETRY_DELAY_MS || "5000", 10);
const FRAMEBUF_WAIT_MS = parseInt(process.env.NOVNC_FRAMEBUF_WAIT_MS || "10000", 10);
const FRAMEBUF_MIN_MEAN = parseFloat(process.env.NOVNC_FRAMEBUF_MIN_MEAN || "4");

async function waitConnected(page, label, setup) {
  let lastErr;
  for (let attempt = 0; attempt < RETRIES; attempt++) {
    try {
      await page.goto(setup.url, {
        waitUntil: "domcontentloaded",
        timeout: 45_000,
      });
      await page.waitForFunction(
        () => {
          const el = document.querySelector("#noVNC_status");
          return el && /connected/i.test((el.textContent || "").trim());
        },
        { timeout: 45_000 }
      );
      const t = await page.$eval("#noVNC_status", (el) => el.textContent || "");
      console.log(`[ok] ${label}: ${t.trim().slice(0, 80)}`);
      return;
    } catch (e) {
      lastErr = e;
      if (attempt < RETRIES - 1) {
        console.warn(`[retry ${attempt + 1}/${RETRIES}] ${label}: ${e.message}`);
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
      }
    }
  }
  throw lastErr;
}

function canvasMeanRgb(page) {
  return page.evaluate(() => {
    const c =
      document.querySelector("#noVNC_canvas") || document.querySelector("canvas");
    if (!c || !c.getContext) return -1;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (!ctx) return -1;
    const w = Math.min(320, c.width || 0);
    const h = Math.min(240, c.height || 0);
    if (w < 2 || h < 2) return -1;
    let data;
    try {
      data = ctx.getImageData(0, 0, w, h).data;
    } catch {
      return -1;
    }
    let s = 0;
    let n = 0;
    for (let i = 0; i < data.length; i += 16) {
      s += data[i] + data[i + 1] + data[i + 2];
      n += 3;
    }
    return n ? s / n : -1;
  });
}

async function main() {
  const browser = await puppeteer.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 900 });
    await waitConnected(page, "GET /", { url: `${base}/` });

    const page2 = await browser.newPage();
    await page2.setViewport({ width: 1280, height: 900 });
    await waitConnected(page2, "GET /vnc_auto.html", {
      url: `${base}/vnc_auto.html?autoconnect=true&resize=scale`,
    });

    console.log(
      `[wait] ${FRAMEBUF_WAIT_MS}ms for RFB frames after warm Chromium…`
    );
    await new Promise((r) => setTimeout(r, FRAMEBUF_WAIT_MS));

    const reportsDir = path.join(__dirname, "..", "reports");
    fs.mkdirSync(reportsDir, { recursive: true });
    const shot = path.join(reportsDir, "novnc_puppeteer_after_warm.png");
    await page.screenshot({ path: shot, fullPage: true });
    console.log(`[screenshot] ${shot}`);

    const mean = await canvasMeanRgb(page);
    if (mean < 0) {
      throw new Error(
        "noVNC canvas missing or getImageData failed (check renderer path)"
      );
    }
    if (mean < FRAMEBUF_MIN_MEAN) {
      throw new Error(
        `VNC framebuffer too dark (mean RGB ${mean.toFixed(2)} < ${FRAMEBUF_MIN_MEAN}); black screen?`
      );
    }
    console.log(`[ok] canvas mean RGB (sampled) ≈ ${mean.toFixed(2)}`);

    const stRes = await fetch(`${c3Api}/status`);
    const st = await stRes.json();
    if (st.status !== "ok" || st.browser !== "running" || (st.open_pages || 0) < 1) {
      throw new Error(`GET /status unexpected: ${JSON.stringify(st)}`);
    }
    console.log("[ok] GET /status:", st);
  } finally {
    await browser.close();
  }
  console.log("validate_novnc.mjs: all checks passed");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
