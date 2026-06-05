// API client for the backend. The main entry is askStream() which calls the
// backend /ask endpoint (intent router) and streams SSE events back.

/**
 * Stream the /ask endpoint. Calls onEvent(event) for every SSE event:
 *   {stage:"routing"|"routed"|"report_generating"|"analyze"|"sql"|"execute"|"interpret"|"complete", data:{...}}
 * Resolves with the final "complete" event's data (or null).
 */
export async function askStream(question, conversationId, onEvent) {
  const res = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  return finalData;
}

/** Generate a full report directly (used by the "Generate Report" button). */
export async function generateReport(question) {
  const res = await fetch("/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error("Report generation failed");
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data;
}

/** Persist a generated report to localStorage and open the backend report view. */
export function openReport(question, reportData) {
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
  window.open(`/report-view?id=${reportId}`, "_blank");
  return reportId;
}
