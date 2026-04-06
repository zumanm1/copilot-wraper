/**
 * C3 /setup: #openPortalBtn exists, screenshots, click → #navMsg shows success.
 * Usage: BROWSER_AUTH_URL=http://127.0.0.1:8001 node validate_c3_setup_button.mjs
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const c3Api = (process.env.BROWSER_AUTH_URL || "http://127.0.0.1:8001").replace(
  /\/$/,
  ""
);

async function main() {
  const browser = await puppeteer.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 900 });
    await page.goto(`${c3Api}/setup`, {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });

    const btn = await page.$("#openPortalBtn");
    if (!btn) {
      throw new Error("missing #openPortalBtn on /setup");
    }

    const reportsDir = path.join(__dirname, "..", "reports");
    fs.mkdirSync(reportsDir, { recursive: true });
    const before = path.join(reportsDir, "c3_setup_puppeteer_before_open_portal.png");
    const after = path.join(reportsDir, "c3_setup_puppeteer_after_open_portal.png");
    await page.screenshot({ path: before, fullPage: true });
    console.log(`[screenshot] ${before}`);

    await btn.click();

    await page.waitForFunction(
      () => {
        const el = document.getElementById("navMsg");
        const t = (el && el.textContent) || "";
        return /opened in vnc browser/i.test(t);
      },
      { timeout: 90_000 }
    );

    await page.screenshot({ path: after, fullPage: true });
    console.log(`[screenshot] ${after}`);

    const text = await page.$eval("#navMsg", (el) => el.textContent || "");
    console.log(`[ok] navMsg: ${text.trim().slice(0, 120)}`);
  } finally {
    await browser.close();
  }
  console.log("validate_c3_setup_button.mjs: all checks passed");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
