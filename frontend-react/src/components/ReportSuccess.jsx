// Shown after a report has ALREADY been generated + stored under reportId.
// The button just RE-OPENS the stored report tab — it does NOT regenerate
// (no backend call), matching the vanilla "Open Report" success card.
export default function ReportSuccess({ reportId, t }) {
  return (
    <div
      style={{
        marginTop: "0.6rem", display: "flex", alignItems: "center", gap: "0.75rem",
        padding: "0.7rem 0.9rem", border: `1px solid ${t.border}`, borderRadius: 12, background: t.bgCard,
      }}
    >
      <div style={{ color: "#22c55e" }}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M22 11.08V12a10 10 0 11-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" />
        </svg>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "0.85rem", color: t.text }}>Report ready</div>
        <div style={{ fontSize: "0.76rem", color: t.textMuted }}>Opened in a new tab.</div>
      </div>
      <button
        onClick={() => window.open(`/report-view?id=${reportId}`, "_blank")}
        style={{
          border: "none", borderRadius: 8, padding: "0.5rem 0.9rem", cursor: "pointer",
          color: "#fff", fontSize: "0.8rem", fontWeight: 600, whiteSpace: "nowrap",
          background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`,
        }}
      >
        Open Report ↗
      </button>
    </div>
  );
}
