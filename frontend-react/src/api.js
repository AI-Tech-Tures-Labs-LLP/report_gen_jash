// API client for the backend. The main entry is askStream() which calls the
// backend /ask endpoint (intent router) and streams SSE events back.

import { getAuthHeaders } from "./auth.js";
import { getHardcodedReport, getHardcodedSqlQuery } from "./hardcodedReports.js";

/**
 * Pretty-print the pipeline cost/speed metrics to the dev-tools console.
 * Backend attaches `metrics` (from _build_metrics) to every chat + report
 * response. We surface the REAL numbers — USD cost, wall-clock seconds, token
 * counts, cache hit-rate, and a per-agent table — not a true/false summary.
 */
export function logMetrics(metrics, source = "request") {
  if (!metrics || typeof metrics !== "object") return;
  const usd = Number(metrics.estimated_cost_usd || 0);
  const secs = (Number(metrics.total_time_ms || 0) / 1000).toFixed(1);
  const fmtN = (n) => Number(n || 0).toLocaleString("en-US");

  const title =
    `%c⚡ ${source} cost & speed  ·  $${usd.toFixed(6)}  ·  ${secs}s  ·  ` +
    `${fmtN(metrics.total_tokens)} tokens  ·  cache ${metrics.cache_hit_rate_pct ?? 0}%`;
  console.groupCollapsed(title, "color:#7c3aed;font-weight:bold");

  console.log(
    `%cTotals%c  cost=$${usd.toFixed(6)}   time=${secs}s   agent_calls=${metrics.agent_calls ?? 0}`,
    "color:#0ea5e9;font-weight:bold", "color:inherit"
  );
  console.log(
    `Tokens  in=${fmtN(metrics.total_input_tokens)}  out=${fmtN(metrics.total_output_tokens)}  ` +
    `cache_read=${fmtN(metrics.total_cache_read_tokens)}  cache_write=${fmtN(metrics.total_cache_creation_tokens)}  ` +
    `(hit-rate ${metrics.cache_hit_rate_pct ?? 0}%)`
  );

  // Per-agent breakdown as a real table (sortable, expandable in dev-tools).
  if (Array.isArray(metrics.agents) && metrics.agents.length) {
    const rows = metrics.agents.map((a) => ({
      agent: a.agent,
      model: a.model,
      "time (s)": (Number(a.elapsed_ms || 0) / 1000).toFixed(1),
      rounds: a.tool_rounds,
      in: a.input_tokens,
      out: a.output_tokens,
      cache_read: a.cache_read_tokens,
      "cost ($)": Number(a.cost_usd || 0).toFixed(6),
    }));
    console.table(rows);
  }
  console.groupEnd();
}

/**
 * Stream the /ask endpoint. Calls onEvent(event) for every SSE event:
 *   {stage:"routing"|"routed"|"report_generating"|"analyze"|"sql"|"execute"|"interpret"|"complete", data:{...}}
 * Resolves with the final "complete" event's data (or null).
 */
export async function askStream(question, conversationId, onEvent) {
  // Short-circuit for hardcoded SQL queries — resolves instantly, no backend call.
  const hardcodedSql = getHardcodedSqlQuery(question);
  if (hardcodedSql) {
    return new Promise((resolve) =>
      setTimeout(() => {
        if (onEvent) {
          onEvent({ stage: "routed",   data: { mode: "sql" } });
          onEvent({ stage: "complete", data: hardcodedSql });
        }
        resolve(hardcodedSql);
      }, 350)
    );
  }

  const res = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ question, conversation_id: conversationId }),
  });
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalData = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(line.slice(6));
        if (event.stage === "complete") finalData = event.data;
        if (onEvent) onEvent(event);
      } catch {
        /* ignore malformed chunk */
      }
    }
  }
  if (finalData && finalData.metrics) logMetrics(finalData.metrics, "chat");
  return finalData;
}

/** Generate a full report directly (used by the "Generate Report" button). */
export async function generateReport(question) {
  const hardcoded = getHardcodedReport(question);
  if (hardcoded) {
    return new Promise((resolve) => setTimeout(() => resolve(hardcoded), 400));
  }

  const res = await fetch("/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    // Extract the real error detail from FastAPI's error response
    const errBody = await res.json().catch(() => ({}));
    const detail = errBody.detail || errBody.error || "Report generation failed";
    // Friendly message for the API quota limit error
    if (typeof detail === "string" && detail.includes("API usage limits")) {
      const match = detail.match(/2026-\d{2}-\d{2}/);
      const date = match ? match[0] : "soon";
      throw new Error(`⚠️ Claude API usage limit reached. Access will be restored on ${date}. Please try again later.`);
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  if (data.metrics) logMetrics(data.metrics, "report");
  return data;
}

/** Persist a generated report to localStorage AND MongoDB, then open the view. */
export function openReport(question, reportData, convId) {
  const reportId = "rpt_" + Date.now();
  try {
    localStorage.setItem("sqlbot_report_" + reportId, JSON.stringify(reportData));
    localStorage.setItem("sqlbot_report_" + reportId + "_question", question);
    localStorage.setItem(
      "sqlbot_report_" + reportId + "_theme",
      document.documentElement.getAttribute("data-theme") || "light"
    );
  } catch {
    /* storage full — still try to open */
  }
  // Sync report to MongoDB in the background
  syncReportToMongo(reportId, question, reportData, convId);
  window.open(`/report-view?id=${reportId}`, "_blank");
  return reportId;
}

/** Save report to MongoDB via the backend API. */
async function syncReportToMongo(reportId, question, reportData, convId) {
  try {
    const { getAuthHeaders } = await import("./auth.js");
    const res = await fetch("/reports/save", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({
        report_id: reportId,
        conv_id: convId || null,
        question,
        report_data: reportData,
      }),
    });
    if (!res.ok) console.warn("Failed to sync report to MongoDB:", res.status);
  } catch (err) {
    console.warn("Report sync to MongoDB failed:", err);
  }
}
