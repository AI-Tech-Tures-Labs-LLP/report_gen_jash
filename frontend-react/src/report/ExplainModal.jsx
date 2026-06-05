// Explanation modal (eye button): what / how / why / insight + SQL.
export default function ExplainModal({ title, explanation, sql, t, onClose }) {
  if (!explanation && !sql) return null;
  const e = explanation || {};
  const sections = [
    ["What it shows", e.what, "#3b82f6"],
    ["How it's built", e.how, "#8b5cf6"],
    ["Why it matters", e.why, "#d4af37"],
    ["Key insight", e.insight, "#f59e0b"],
  ].filter(([, v]) => v);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 1000,
        display: "grid", placeItems: "center", padding: "1rem",
      }}
    >
      <div
        onClick={(ev) => ev.stopPropagation()}
        style={{
          background: t.bgCard, color: t.text, border: `1px solid ${t.border}`,
          borderRadius: 16, padding: "1.5rem", maxWidth: 620, width: "100%",
          maxHeight: "85vh", overflowY: "auto", backdropFilter: "blur(16px)", boxShadow: t.shadow,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
          <h3 style={{ fontSize: "1.1rem" }}>{title}</h3>
          <button onClick={onClose} style={{ border: "none", background: "transparent", color: t.textMuted, cursor: "pointer", fontSize: "1.3rem" }}>×</button>
        </div>
        {sections.map(([label, val, color]) => (
          <div key={label} style={{ marginBottom: "0.9rem" }}>
            <div style={{ fontSize: "0.72rem", fontWeight: 700, color, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: "0.25rem" }}>{label}</div>
            <div style={{ fontSize: "0.88rem", lineHeight: 1.55, color: t.text }}>{val}</div>
          </div>
        ))}
        {sql && (
          <div>
            <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "#06b6d4", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: "0.25rem" }}>SQL query used</div>
            <pre style={{ background: t.code, color: t.codeText, padding: "0.75rem", borderRadius: 8, fontSize: "0.76rem", overflowX: "auto", fontFamily: "'JetBrains Mono', monospace", whiteSpace: "pre-wrap" }}>{sql}</pre>
          </div>
        )}
      </div>
    </div>
  );
}
