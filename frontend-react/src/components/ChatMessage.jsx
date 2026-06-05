import { useState } from "react";

// Renders one chat message (user or AI). AI messages show the answer + collapsible
// SQL / Results / Insights sections, mirroring the vanilla app.
export default function ChatMessage({ msg, t }) {
  if (msg.role === "user") {
    return (
      <div style={{ display: "flex", justifyContent: "flex-end", margin: "0.6rem 0" }}>
        <div
          style={{
            background: t.userBubble,
            color: t.userText,
            padding: "0.7rem 1rem",
            borderRadius: "14px 14px 4px 14px",
            maxWidth: "75%",
            fontSize: "0.92rem",
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
          }}
        >
          {msg.text}
        </div>
      </div>
    );
  }

  const d = msg.data || {};
  return (
    <div style={{ display: "flex", gap: "0.6rem", margin: "0.6rem 0", alignItems: "flex-start" }}>
      <Avatar t={t} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {msg.error ? (
          <div style={{ color: "#ef4444", fontSize: "0.9rem" }}>{msg.error}</div>
        ) : (
          <>
            {d.answer && (
              <div style={{ color: t.text, fontSize: "0.92rem", lineHeight: 1.6, marginBottom: "0.5rem" }}>
                {d.answer}
              </div>
            )}
            {d.sql && <Collapsible title="SQL Query" t={t} defaultOpen mono>{d.sql}</Collapsible>}
            {Array.isArray(d.data) && d.data.length > 0 && (
              <Collapsible title={`Results (${d.data.length} rows)`} t={t} defaultOpen>
                <ResultTable rows={d.data} t={t} />
              </Collapsible>
            )}
            {d.insights && <Collapsible title="Insights" t={t}>{d.insights}</Collapsible>}
          </>
        )}
      </div>
    </div>
  );
}

function Avatar({ t }) {
  return (
    <div
      style={{
        width: 30, height: 30, borderRadius: 8, flexShrink: 0,
        background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`,
        display: "grid", placeItems: "center", color: "#fff",
      }}
    >
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
      </svg>
    </div>
  );
}

function Collapsible({ title, children, t, defaultOpen = false, mono = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ border: `1px solid ${t.border}`, borderRadius: 10, marginTop: "0.5rem", overflow: "hidden" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%", textAlign: "left", padding: "0.5rem 0.75rem",
          background: "transparent", color: t.textMuted, border: "none",
          cursor: "pointer", fontSize: "0.78rem", fontWeight: 600,
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}
      >
        <span>{title}</span>
        <span style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}>▶</span>
      </button>
      {open && (
        <div
          style={{
            padding: "0.5rem 0.75rem", borderTop: `1px solid ${t.border}`,
            fontSize: "0.82rem", color: t.text, lineHeight: 1.5,
            ...(mono
              ? { fontFamily: "'JetBrains Mono', monospace", background: t.code, color: t.codeText, whiteSpace: "pre-wrap", overflowX: "auto" }
              : { whiteSpace: "pre-wrap" }),
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}

function ResultTable({ rows, t }) {
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.78rem" }}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} style={{ textAlign: "left", padding: "0.35rem 0.5rem", borderBottom: `1px solid ${t.border}`, color: t.textMuted, fontWeight: 600 }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 50).map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} style={{ padding: "0.35rem 0.5rem", borderBottom: `1px solid ${t.border}`, color: t.text }}>
                  {String(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 50 && (
        <div style={{ color: t.textMuted, fontSize: "0.72rem", padding: "0.4rem" }}>
          Showing first 50 of {rows.length} rows
        </div>
      )}
    </div>
  );
}
