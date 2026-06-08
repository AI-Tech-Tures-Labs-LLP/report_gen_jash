import { useState, useEffect } from "react";

// Global filter bar. Loads option values from /report/filters and applies
// filters via /report/apply-filters (server-side WHERE injection, no LLM).
export default function FilterBar({ applicable, report, onApplied, t }) {
  const [opts, setOpts] = useState({ categories: [], customers: [], products: [], statuses: [], date_range: {} });
  const [f, setF] = useState({ date_from: "", date_to: "", category: "", customer: "", status: "", product: "" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/report/filters")
      .then((r) => r.json())
      .then(setOpts)
      .catch(() => {});
  }, []);

  async function apply() {
    setBusy(true);
    try {
      const body = { report, ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)) };
      const res = await fetch("/report/apply-filters", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data && data.report) onApplied(data);
    } catch { /* */ } finally { setBusy(false); }
  }

  const sel = (key, list, label) =>
    applicable[key === "status" ? "status" : key] || (key === "category" && applicable.category) ? (
      <select value={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.value })} style={inp(t)}>
        <option value="">{label}</option>
        {list.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    ) : null;

  function clearAll() {
    setF({ date_from: "", date_to: "", category: "", customer: "", status: "", product: "" });
  }

  return (
    <div style={{
      background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 8,
      padding: "0.45rem 0.75rem", marginBottom: "0.65rem",
      display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem",
    }}>
      {applicable.date_range && (
        <InlineField label="DATE RANGE" t={t}>
          <input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} className="rpt-filter-input" style={inp(t)} />
          <span style={{ color: t.textMuted, fontSize: "0.7rem" }}>–</span>
          <input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} className="rpt-filter-input" style={inp(t)} />
        </InlineField>
      )}
      {applicable.category && (
        <InlineField label="CATEGORY" t={t}>
          <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} className="rpt-filter-input" style={inp(t)}>
            <option value="">All</option>
            {opts.categories.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </InlineField>
      )}
      {applicable.status && (
        <InlineField label="STATUS" t={t}>
          <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} className="rpt-filter-input" style={inp(t)}>
            <option value="">All</option>
            {opts.statuses.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </InlineField>
      )}
      {applicable.customer && (
        <InlineField label="CUSTOMER" t={t}>
          <select value={f.customer} onChange={(e) => setF({ ...f, customer: e.target.value })} className="rpt-filter-input" style={inp(t)}>
            <option value="">All</option>
            {opts.customers.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </InlineField>
      )}
      {applicable.product && (
        <InlineField label="PRODUCT" t={t}>
          <select value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })} className="rpt-filter-input" style={inp(t)}>
            <option value="">All</option>
            {opts.products.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </InlineField>
      )}
      <div style={{ display: "flex", gap: "0.35rem", alignItems: "center", marginLeft: "auto" }}>
        <button onClick={apply} disabled={busy} style={{
          border: "none", borderRadius: 6, padding: "0.32rem 0.85rem",
          cursor: busy ? "not-allowed" : "pointer", color: "#fff",
          background: "#f59e0b",
          fontSize: "0.76rem", fontWeight: 700, opacity: busy ? 0.65 : 1, whiteSpace: "nowrap",
        }}>
          {busy ? "Applying…" : "✦ Apply Filters"}
        </button>
        <button onClick={clearAll} style={{
          border: `1px solid ${t.border}`, background: "transparent",
          color: t.textMuted, borderRadius: 6, padding: "0.32rem 0.6rem",
          cursor: "pointer", fontSize: "0.73rem", fontWeight: 500, whiteSpace: "nowrap",
        }}>
          Clear Filters
        </button>
      </div>
    </div>
  );
}

function inp(t) {
  return {
    border: `1px solid ${t.border}`, background: t.bgPanel, color: t.text,
    borderRadius: 5, padding: "0.25rem 0.45rem", fontSize: "0.76rem",
    outline: "none", transition: "border-color 0.15s, box-shadow 0.15s", minWidth: 0,
  };
}
function InlineField({ label, t, children }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
      <span style={{ fontSize: "0.65rem", fontWeight: 700, letterSpacing: "0.06em", color: t.textMuted, whiteSpace: "nowrap" }}>{label}</span>
      {children}
    </div>
  );
}
