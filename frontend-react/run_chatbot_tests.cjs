// Drives the real chatbot UI in a browser, asks each question, watches the
// SSE stream for the "routed" event to detect report-mode early and abort,
// otherwise waits for completion and reads cost/time from console logMetrics.
//
// Usage: node run_chatbot_tests.js <questionsJsonPath> <outputJsonPath> [concurrency]

const { chromium } = require("playwright");
const fs = require("fs");

const FRONTEND_URL = "http://localhost:5173";
const EMAIL = "admin@gmail.com";
const PASSWORD = "admin@gmail.com";

const [, , questionsPath, outputPath, concurrencyArg] = process.argv;
const CONCURRENCY = parseInt(concurrencyArg || "4", 10);
const HARD_TIMEOUT_MS = 150000; // hard ceiling per question, independent of internal poll loop

function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`HARD_TIMEOUT after ${ms}ms: ${label}`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function appendResult(outputPath, result) {
  const line = JSON.stringify(result) + "\n";
  fs.appendFileSync(outputPath + ".ndjson", line);
}

async function loginNewContext(browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" });

  // If already on chat (persisted storage some other way), skip login.
  const emailInput = page.locator('input[type="email"]');
  if (await emailInput.count()) {
    await emailInput.fill(EMAIL);
    await page.locator('input[type="password"]').fill(PASSWORD);
    await page.locator('button[type="submit"]').click();
    await page.waitForSelector('textarea[placeholder="Ask a question about your data…"]', { timeout: 30000 });
  } else {
    await page.waitForSelector('textarea[placeholder="Ask a question about your data…"]', { timeout: 30000 });
  }
  return { context, page };
}

async function askOne(page, question) {
  const result = {
    question_id: question.id,
    question: question.text,
    mode: null,
    aborted: false,
    cost: null,
    time_s: null,
    error: null,
  };

  let resolveDone;
  const done = new Promise((res) => { resolveDone = res; });
  let settled = false;
  const finish = () => { if (!settled) { settled = true; resolveDone(); } };

  const onResponse = async (response) => {
    try {
      if (!response.url().endsWith("/ask")) return;
      const req = response.request();
      if (req.resourceType() !== "fetch" && req.resourceType() !== "xhr") return;
      // Stream the body ourselves to detect "routed" -> mode=report ASAP.
      const body = await response.body().catch(() => null);
      if (!body) return;
      const text = body.toString("utf-8");
      const chunks = text.split("\n\n").filter((l) => l.startsWith("data: "));
      for (const chunk of chunks) {
        try {
          const evt = JSON.parse(chunk.slice(6));
          if (evt.stage === "routed" && evt.data?.mode) {
            result.mode = evt.data.mode;
          }
          if (evt.stage === "complete" && evt.data?.metrics) {
            result.cost = Number(evt.data.metrics.estimated_cost_usd || 0);
            result.time_s = Number(evt.data.metrics.total_time_ms || 0) / 1000;
          }
        } catch { /* ignore */ }
      }
    } catch { /* ignore */ }
  };

  const onConsole = (msg) => {
    const text = msg.text();
    if (text.includes("Totals") && text.includes("cost=$")) {
      const costMatch = text.match(/cost=\$([\d.]+)/);
      const timeMatch = text.match(/time=([\d.]+)s/);
      if (costMatch) result.cost = parseFloat(costMatch[1]);
      if (timeMatch) result.time_s = parseFloat(timeMatch[1]);
    }
  };

  page.on("response", onResponse);
  page.on("console", onConsole);

  try {
    const textarea = page.locator('textarea[placeholder="Ask a question about your data…"]');
    await textarea.click();
    await textarea.fill(question.text);
    await textarea.press("Enter");

    // Poll for mode=report (early abort) or completion (Stop button gone / cost captured).
    const maxWaitMs = 140000;
    const pollIntervalMs = 300;
    const start = Date.now();
    while (Date.now() - start < maxWaitMs) {
      if (result.mode === "report") {
        // Abort immediately via the Stop button.
        const stopBtn = page.locator('button[title="Stop generating"]');
        if (await stopBtn.count()) {
          await stopBtn.click().catch(() => {});
        }
        result.aborted = true;
        break;
      }
      if (result.cost !== null && result.time_s !== null) {
        break;
      }
      // Also break if the Stop button disappeared (stream ended) even without metrics (e.g. error).
      const stopBtnCount = await page.locator('button[title="Stop generating"]').count();
      if (stopBtnCount === 0 && Date.now() - start > 2000 && result.cost === null && result.mode !== "report") {
        // give a longer grace period for the trailing console.log/network body to land
        await page.waitForTimeout(3000);
        if (result.cost === null) {
          result.error = result.error || "no_metrics_captured_after_stream_end";
          break;
        }
      }
      await page.waitForTimeout(pollIntervalMs);
    }
    if (Date.now() - start >= 120000 && result.cost === null && result.mode !== "report") {
      result.error = result.error || "poll_timeout_120s";
    }
  } catch (err) {
    result.error = String(err);
  } finally {
    page.off("response", onResponse);
    page.off("console", onConsole);
  }

  finish();
  await done;
  return result;
}

async function worker(browser, queue, results) {
  let { context, page } = await loginNewContext(browser);
  while (queue.length) {
    const q = queue.shift();
    if (!q) break;
    process.stdout.write(`[${q.id}] asking...\n`);
    let r;
    try {
      r = await withTimeout(askOne(page, q), HARD_TIMEOUT_MS, q.id);
    } catch (err) {
      r = { question_id: q.id, question: q.text, mode: null, aborted: false, cost: null, time_s: null, error: String(err) };
      // The page/context may be wedged (that's why the hard timeout fired) — recycle it.
      await context.close().catch(() => {});
      ({ context, page } = await loginNewContext(browser));
    }
    results.push(r);
    appendResult(outputPath, r);
    process.stdout.write(`[${q.id}] mode=${r.mode} aborted=${r.aborted} cost=${r.cost} time=${r.time_s} err=${r.error || ""}\n`);
    // Reload to reset chat state between questions (fresh conversation).
    await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" }).catch(() => {});
    await page.waitForSelector('textarea[placeholder="Ask a question about your data…"]', { timeout: 30000 }).catch(() => {});
  }
  await context.close();
}

(async () => {
  const questions = JSON.parse(fs.readFileSync(questionsPath, "utf-8"));
  const queue = [...questions];
  const results = [];
  const browser = await chromium.launch({ headless: true });

  const workers = [];
  for (let i = 0; i < CONCURRENCY; i++) {
    workers.push(worker(browser, queue, results));
  }
  await Promise.all(workers);
  await browser.close();

  fs.writeFileSync(outputPath, JSON.stringify(results, null, 2));
  console.log(`Wrote ${results.length} results to ${outputPath}`);
})();
