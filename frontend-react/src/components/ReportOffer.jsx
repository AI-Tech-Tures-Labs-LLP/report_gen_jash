import { useState } from "react";
import { generateReport, openReport } from "../api.js";

// "Want a detailed analytics report?" card shown under chat answers.
// On click, calls /report and opens the full dashboard in a new tab.
export default function ReportOffer({ question, t, convId }) {
  const [status, setStatus] = useState("idle"); // idle | loading | done | error
  const [hover, setHover] = useState(false);
  const [reportId, setReportId] = useState(null);

  async function handleClick() {
    // Once generated, the button just RE-OPENS the stored report (no second
    // backend call — avoids regenerating an expensive report).
    if (status === "done" && reportId) {
      window.open(`/report-view?id=${reportId}`, "_blank");
      return;
    }
    setStatus("loading");
    try {
      const data = await generateReport(question);
      const id = openReport(question, data, convId); // stores + opens once
      setReportId(id);
      setStatus("done");
    } catch {
      setStatus("error");
    }
  }

  return (
    <div
      style={{
        marginTop: "0.6rem",
        display: "flex",
        alignItems: "center",
        gap: "0.75rem",
        padding: "0.7rem 0.9rem",
        border: `1px solid ${t.border}`,
        borderRadius: 12,
        background: t.bgCard,
      }}
    >
      <div style={{ color: t.accent }}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
          <line x1="3" y1="9" x2="21" y2="9" /><line x1="9" y1="21" x2="9" y2="9" />
        </svg>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "0.85rem", color: t.text }}>
          Want a detailed analytics report?
        </div>
        <div style={{ fontSize: "0.76rem", color: t.textMuted }}>
          Generate a full dashboard with KPIs, charts, tables, and insights.
        </div>
      </div>
      <button
        onClick={handleClick}
        disabled={status === "loading"}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          border: "none",
          borderRadius: 8,
          padding: "0.5rem 0.9rem",
          cursor: status === "loading" ? "default" : "pointer",
          color: "#fff",
          fontSize: "0.8rem",
          fontWeight: 600,
          whiteSpace: "nowrap",
          background:
            status === "error"
              ? "#ef4444"
              : `linear-gradient(135deg, ${t.accent}, ${t.accent2})`,
          opacity: status === "loading" ? 0.7 : hover ? 0.9 : 1,
        }}
      >
        {status === "loading"
          ? "Generating…"
          : status === "done"
          ? "Open Report ↗"
          : status === "error"
          ? "Retry"
          : "Generate Report"}
      </button>
    </div>
  );
}
