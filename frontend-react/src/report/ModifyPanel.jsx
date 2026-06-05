import { useState, useMemo, useRef } from "react";

// Floating "Report Assistant" — sends NL modifications to /report/modify and
// updates the report in place. (Mirrors the vanilla rcFab/rcPanel feature,
// including @mention autocomplete of KPI labels + chart titles.)
const SUGGESTIONS = ["Add a KPI for total orders", "Convert chart 1 to a line chart",
  "Show only the top 5", "Add a profit margin chart"];

export default function ModifyPanel({ report, onUpdate, t }) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [mentionOpen, setMentionOpen] = useState(false);
  const taRef = useRef(null);

  // Mention candidates: KPI labels + chart titles.
  const components = useMemo(() => {
    const out = [];
    (report.kpis || []).filter((k) => !k._placeholder).forEach((k, i) => {
      out.push({ type: "kpi", label: k.label || k.title || `KPI ${i + 1}` });
    });
    (report.charts || []).filter((c) => !c._placeholder).forEach((c, i) => {
      out.push({ type: "chart", label: c.title || `Chart ${i + 1}` });
    });
    return out;
  }, [report]);

  // The text fragment after the most recent unmatched "@".
  const mentionQuery = useMemo(() => {
    const m = /@([^@]*)$/.exec(input);
    return m ? m[1].toLowerCase() : null;
  }, [input]);

  const mentionMatches = useMemo(() => {
    if (mentionQuery === null) return [];
    return components.filter((c) => c.label.toLowerCase().includes(mentionQuery)).slice(0, 8);
  }, [components, mentionQuery]);

  function onInputChange(v) {
    setInput(v);
    setMentionOpen(/@[^@]*$/.test(v) && components.length > 0);
  }

  function insertMention(label) {
    // Replace the trailing "@<query>" with "@<label>: "
    const next = input.replace(/@[^@]*$/, `@${label}: `);
    setInput(next);
    setMentionOpen(false);
    if (taRef.current) taRef.current.focus();
  }

  async function send(text) {
    const mod = (text ?? input).trim();
    if (!mod || busy) return;
    setInput("");
    setMentionOpen(false);
    setMessages((m) => [...m, { role: "user", text: mod }]);
    setBusy(true);
    try {
      const lean = JSON.parse(JSON.stringify(report));
      const res = await fetch("/report/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_json: JSON.stringify(lean), modification: mod, provider: "claude" }),
      });
      const data = await res.json();
      if (!res.ok || data.error || !data.report) throw new Error(data.error || "Modify failed");
      onUpdate(data.report);
      setMessages((m) => [...m, { role: "ai", text: "✅ Updated the report." }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "ai", text: "⚠️ " + (err.message || "Failed.") }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen((o) => !o)}
        title="Ask AI to modify this report"
        style={{
          position: "fixed", bottom: 24, right: 24, zIndex: 1200,
          width: 54, height: 54, borderRadius: "50%", border: "none", cursor: "pointer",
          background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`, color: "#fff",
          boxShadow: t.shadow, display: "grid", placeItems: "center",
        }}
      >
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
        </svg>
      </button>

      {open && (
        <div style={{
          position: "fixed", bottom: 90, right: 24, zIndex: 1200, width: 360, maxHeight: 520,
          background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 14,
          display: "flex", flexDirection: "column", backdropFilter: "blur(16px)", boxShadow: t.shadow,
        }}>
          <div style={{ padding: "0.8rem 1rem", borderBottom: `1px solid ${t.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <strong style={{ fontSize: "0.9rem", color: t.text }}>Report Assistant</strong>
            <button onClick={() => setOpen(false)} style={{ border: "none", background: "transparent", color: t.textMuted, cursor: "pointer", fontSize: "1.2rem" }}>×</button>
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: "0.8rem", minHeight: 120 }}>
            {messages.length === 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)} style={{ border: `1px solid ${t.border}`, background: t.bgPanel, color: t.text, borderRadius: 14, padding: "0.3rem 0.6rem", fontSize: "0.72rem", cursor: "pointer" }}>{s}</button>
                ))}
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", margin: "0.35rem 0" }}>
                <div style={{ maxWidth: "85%", padding: "0.45rem 0.7rem", borderRadius: 10, fontSize: "0.8rem",
                  background: m.role === "user" ? t.userBubble : t.bgPanel, color: m.role === "user" ? t.userText : t.text }}>
                  {m.text}
                </div>
              </div>
            ))}
            {busy && <div style={{ color: t.textMuted, fontSize: "0.78rem", padding: "0.3rem" }}>Working…</div>}
          </div>

          <div style={{ padding: "0.6rem", borderTop: `1px solid ${t.border}`, display: "flex", gap: "0.4rem", position: "relative" }}>
            {mentionOpen && mentionMatches.length > 0 && (
              <div style={{
                position: "absolute", bottom: "100%", left: "0.6rem", right: "0.6rem", marginBottom: 4,
                background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 10, boxShadow: t.shadow,
                maxHeight: 200, overflowY: "auto", zIndex: 10,
              }}>
                {mentionMatches.map((c) => (
                  <button key={c.type + c.label} onMouseDown={(e) => { e.preventDefault(); insertMention(c.label); }}
                    style={{ display: "flex", alignItems: "center", gap: "0.45rem", width: "100%", textAlign: "left", border: "none", background: "transparent", color: t.text, padding: "0.45rem 0.7rem", cursor: "pointer", fontSize: "0.78rem" }}>
                    <span style={{ fontSize: "0.58rem", fontWeight: 700, letterSpacing: "0.03em", color: "#fff", background: c.type === "kpi" ? t.accent : t.accent2, borderRadius: 4, padding: "0.1rem 0.3rem" }}>{c.type === "kpi" ? "KPI" : "CHART"}</span>
                    <span>{c.label}</span>
                  </button>
                ))}
              </div>
            )}
            <textarea
              ref={taRef}
              value={input}
              onChange={(e) => onInputChange(e.target.value)}
              onKeyDown={(e) => {
                if (mentionOpen && mentionMatches.length > 0 && (e.key === "Enter" || e.key === "Tab")) {
                  e.preventDefault(); insertMention(mentionMatches[0].label); return;
                }
                if (e.key === "Escape") { setMentionOpen(false); return; }
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              }}
              rows={1} placeholder="Ask to modify the report…  (type @ to reference a KPI/chart)"
              style={{ flex: 1, resize: "none", border: `1px solid ${t.border}`, borderRadius: 8, padding: "0.45rem", background: t.bgPanel, color: t.text, fontSize: "0.82rem", outline: "none" }}
            />
            <button onClick={() => send()} disabled={busy || !input.trim()}
              style={{ border: "none", borderRadius: 8, padding: "0 0.8rem", cursor: "pointer", color: "#fff",
                background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`, opacity: busy || !input.trim() ? 0.5 : 1 }}>
              ↑
            </button>
          </div>
        </div>
      )}
    </>
  );
}
