import { useState } from "react";

// Placeholder slot used in edit mode. A "+" opens a textarea; submitting POSTs
// /report/modify asking the backend to generate that element at this index.
// Faithful port of _wirePlaceholders from report.js.
export default function PlaceholderCard({ phType, phIdx, report, onUpdate, onRemove, t, autoOpen }) {
  const [openChat, setOpenChat] = useState(!!autoOpen);
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function add() {
    const value = desc.trim();
    if (!value || busy) return;
    setBusy(true);
    setErr("");
    try {
      const clean = JSON.parse(JSON.stringify(report));
      clean.kpis = (clean.kpis || []).filter((k) => !k._placeholder);
      clean.charts = (clean.charts || []).filter((c) => !c._placeholder);
      const total = phType === "kpi" ? clean.kpis.length : clean.charts.length;
      const mod = `Add a new ${phType} at index ${phIdx} (0-based, currently ${total} ${phType}s exist): ${value}`;
      const res = await fetch("/report/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_json: JSON.stringify(clean), modification: mod, provider: "claude" }),
      });
      const data = await res.json();
      if (data.error || !data.report) { setErr(data.error || "Failed — please try again."); setBusy(false); return; }
      onUpdate(data.report);
    } catch (e) {
      setErr("Network error: " + String(e));
      setBusy(false);
    }
  }

  const isKpi = phType === "kpi";
  return (
    <div style={{
      background: "transparent", border: `2px dashed ${t.border}`, borderRadius: 12,
      padding: "1rem", position: "relative", minHeight: isKpi ? 92 : 200,
      display: "flex", flexDirection: "column", justifyContent: "center", gridColumn: "auto",
    }}>
      <button title="Remove empty slot" onClick={onRemove}
        style={{ position: "absolute", top: 6, right: 6, border: `1px solid ${t.border}`, background: t.bgPanel, color: t.textMuted, borderRadius: 6, width: 22, height: 22, cursor: "pointer", fontSize: "0.7rem", lineHeight: 1 }}>✕</button>

      {!openChat ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "0.5rem" }}>
          <button title={`Add ${phType} here`} onClick={() => setOpenChat(true)}
            style={{ width: 38, height: 38, borderRadius: "50%", border: `1px solid ${t.accent}`, background: t.bgPanel, color: t.accent, cursor: "pointer", fontSize: "1.3rem", lineHeight: 1 }}>+</button>
          <span style={{ fontSize: "0.74rem", color: t.textMuted }}>Add {phType} here</span>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          <textarea
            autoFocus value={desc} onChange={(e) => setDesc(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); add(); }
              if (e.key === "Escape") { setOpenChat(false); setDesc(""); }
            }}
            rows={2}
            placeholder={isKpi ? 'Describe the KPI you want (e.g. "average order value")…' : "Describe the chart you want…"}
            style={{ resize: "none", border: `1px solid ${t.border}`, borderRadius: 8, padding: "0.5rem", background: t.bgPanel, color: t.text, fontSize: "0.8rem", outline: "none" }}
          />
          {err && <div style={{ fontSize: "0.72rem", color: "#ef4444" }}>{err}</div>}
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <button onClick={add} disabled={busy || !desc.trim()}
              style={{ border: "none", borderRadius: 8, padding: "0.35rem 0.8rem", cursor: "pointer", color: "#fff", background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`, fontSize: "0.76rem", fontWeight: 600, opacity: busy || !desc.trim() ? 0.5 : 1 }}>
              {busy ? "Adding…" : "Add"}
            </button>
            <button onClick={() => { setOpenChat(false); setDesc(""); }} style={{ border: `1px solid ${t.border}`, background: t.bgPanel, color: t.text, borderRadius: 8, padding: "0.35rem 0.7rem", cursor: "pointer", fontSize: "0.76rem" }}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
