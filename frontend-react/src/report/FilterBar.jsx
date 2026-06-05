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

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center", background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 12, padding: "0.7rem 0.9rem", marginBottom: "1.4rem" }}>
      {applicable.date_range && (
        <>
          <input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} style={inp(t)} />
          <span style={{ color: t.textMuted, fontSize: "0.8rem" }}>to</span>
          <input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} style={inp(t)} />
        </>
      )}
      {applicable.category && sel("category", opts.categories, "All categories")}
      {applicable.status && sel("status", opts.statuses, "All statuses")}
      {applicable.customer && sel("customer", opts.customers, "All customers")}
      {applicable.product && sel("product", opts.products, "All products")}
      <button onClick={apply} disabled={busy} style={{ border: "none", borderRadius: 8, padding: "0.45rem 0.9rem", cursor: "pointer", color: "#fff", background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`, fontSize: "0.8rem", fontWeight: 600, opacity: busy ? 0.6 : 1 }}>
        {busy ? "Applying…" : "Apply Filters"}
      </button>
    </div>
  );
}

function inp(t) {
  return { border: `1px solid ${t.border}`, background: t.bgPanel, color: t.text, borderRadius: 8, padding: "0.4rem 0.6rem", fontSize: "0.8rem", outline: "none" };
}
