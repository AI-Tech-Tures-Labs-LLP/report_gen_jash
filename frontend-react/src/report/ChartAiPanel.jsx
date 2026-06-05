import { useState } from "react";

// Per-chart AI modify panel. Faithful port of _chartAiSend from report.js:
// builds a chart-scoped command, strips heavy data, POSTs /report/modify,
// then hands the new report back to the parent via onUpdate.
const SUGGESTIONS = [
  { q: "convert to line chart", label: "convert to line" },
  { q: "convert to bar chart", label: "convert to bar" },
  { q: "convert to pie chart", label: "convert to pie" },
  { q: "show top 5", label: "top 5" },
  { q: "show top 10", label: "top 10" },
];

export default function ChartAiPanel({ chartIdx, report, onUpdate, onClose, t }) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);

  async function send(text) {
    const value = (text ?? input).trim();
    if (!value || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: value }]);
    setBusy(true);

    try {
      const chartSpec = (report.charts || [])[chartIdx];
      const chartTitle = chartSpec ? (chartSpec.title || `chart ${chartIdx}`) : `chart ${chartIdx}`;
      const chartDataKeys = chartSpec && Array.isArray(chartSpec.data) && chartSpec.data.length > 0
        ? Object.keys(chartSpec.data[0]).join(", ") : "";
      const dataContext = chartDataKeys ? ` [columns: ${chartDataKeys}, rows: ${chartSpec.data.length}]` : "";
      const command = `For the chart titled "${chartTitle}"${dataContext}: ${value}`;

      const clean = JSON.parse(JSON.stringify(report));
      clean.kpis = (clean.kpis || []).filter((k) => !k._placeholder);
      clean.charts = (clean.charts || []).filter((c) => !c._placeholder);
      if (clean.charts) clean.charts.forEach((c) => { delete c.data; });
      if (clean.kpis) clean.kpis.forEach((k) => { delete k.value; });

      const res = await fetch("/report/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_json: JSON.stringify(clean), modification: command, provider: "claude" }),
      });
      if (!res.ok) throw new Error("Server error " + res.status);
      const data = await res.json();
      if (data.error || !data.report) throw new Error(data.error || "Modification failed.");

      setMessages((m) => [...m, { role: "ai", text: "✓ " + (data.summary || "Chart updated successfully."), ok: true }]);
      onUpdate(data.report);
    } catch (err) {
      setMessages((m) => [...m, { role: "ai", text: "❌ " + (err.message || "Modification failed."), ok: false }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{
      marginTop: "0.6rem", border: `1px solid ${t.border}`, borderRadius: 10,
      background: t.bgPanel, overflow: "hidden",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0.5rem 0.7rem", borderBottom: `1px solid ${t.border}` }}>
        <span style={{ fontSize: "0.7rem", fontWeight: 700, letterSpacing: "0.04em", color: t.textMuted }}>AI MODIFY</span>
        <button onClick={onClose} style={{ border: "none", background: "transparent", color: t.textMuted, cursor: "pointer", fontSize: "0.95rem", lineHeight: 1 }}>×</button>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem", padding: "0.5rem 0.7rem" }}>
        {SUGGESTIONS.map((s) => (
          <button key={s.q} onClick={() => setInput(s.q)} style={{ border: `1px solid ${t.border}`, background: t.bgCard, color: t.text, borderRadius: 12, padding: "0.2rem 0.55rem", fontSize: "0.7rem", cursor: "pointer" }}>{s.label}</button>
        ))}
      </div>

      {messages.length > 0 && (
        <div style={{ maxHeight: 140, overflowY: "auto", padding: "0 0.7rem", display: "flex", flexDirection: "column", gap: "0.3rem" }}>
          {messages.map((m, i) => (
            <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
              <div style={{
                maxWidth: "85%", padding: "0.35rem 0.6rem", borderRadius: 9, fontSize: "0.75rem",
                background: m.role === "user" ? t.userBubble : t.bgCard,
                color: m.role === "user" ? t.userText : (m.ok === false ? "#ef4444" : t.text),
              }}>{m.text}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: "0.4rem", padding: "0.5rem 0.7rem" }}>
        <textarea
          value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          rows={1} placeholder='e.g. "show top 5", "convert to line", "filter Mumbai"'
          style={{ flex: 1, resize: "none", border: `1px solid ${t.border}`, borderRadius: 8, padding: "0.4rem", background: t.bgCard, color: t.text, fontSize: "0.78rem", outline: "none" }}
        />
        <button onClick={() => send()} disabled={busy || !input.trim()}
          style={{ border: "none", borderRadius: 8, padding: "0 0.7rem", cursor: "pointer", color: "#fff", background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`, opacity: busy || !input.trim() ? 0.5 : 1 }}>↑</button>
      </div>
    </div>
  );
}
