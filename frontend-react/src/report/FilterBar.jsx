import { useState, useEffect, useRef } from "react";

// Human labels for known filter names across all domains. Unknown names fall
// back to a title-cased version of the key.
const FILTER_LABELS = {
  category: "CATEGORY", status: "STATUS", customer: "CUSTOMER", product: "PRODUCT",
  vendor: "VENDOR", po_type: "PO TYPE", gold_kt: "GOLD KARAT",
  stage: "STAGE", territory: "TERRITORY", hunter: "HUNTER",
  payment_status: "PAYMENT STATUS",
};

function labelFor(key) {
  return FILTER_LABELS[key] || key.toUpperCase().replace(/_/g, " ");
}

// Global filter bar. Domain-aware: `applicable._domain` selects the filter
// vocabulary + option set. Text filters are Excel-style multi-select checkbox
// dropdowns (tick as many values as you want → SQL IN (...)); date range is a
// single from/to pair. Filtering is server-side (no LLM).
export default function FilterBar({ applicable, report, onApplied, t }) {
  const domain = applicable._domain || "sales";

  // Which text-filter fields apply to this report (exclude _domain + date_range).
  const fieldNames = Object.keys(applicable).filter(
    (k) => k !== "_domain" && k !== "date_range" && applicable[k]
  );

  const [options, setOptions] = useState({});           // {name: [values]}
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [optionsError, setOptionsError] = useState("");
  const [selected, setSelected] = useState({});          // {name: Set(values)}
  const [dates, setDates] = useState({ date_from: "", date_to: "" });
  const [openKey, setOpenKey] = useState(null);          // which dropdown is open
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const requestSeq = useRef(0);
  const optionsSeq = useRef(0);

  // CASCADING options: every time a selection changes, re-fetch every
  // dropdown's option list scoped by the OTHER currently-selected filters —
  // so a dropdown can never offer a value that would combine with what's
  // already picked to match zero rows. This is what prevents the "random
  // filters → every chart errors/vanishes" failure mode: you simply can't
  // construct an impossible combination through the UI anymore.
  useEffect(() => {
    const mySeq = ++optionsSeq.current;
    setOptionsLoading(true);
    const body = {
      domain,
      filters: Object.fromEntries(
        Object.entries(selected).filter(([, v]) => v && v.size > 0).map(([k, v]) => [k, [...v]])
      ),
    };
    if (dates.date_from) body.date_from = dates.date_from;
    if (dates.date_to) body.date_to = dates.date_to;

    fetch("/report/filters", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`Server error ${r.status}`);
        return r.json();
      })
      .then((d) => {
        if (mySeq !== optionsSeq.current) return;  // a newer selection superseded this fetch
        setOptionsError("");
        setOptionsLoading(false);
        const newOptions = d.options || {};
        setOptions(newOptions);
        // Drop any previously-picked value that's no longer offered for its
        // dropdown (its combination with the other filters would now match
        // zero rows) — keeps every selection always valid.
        setSelected((prev) => {
          let changed = false;
          const next = {};
          for (const [key, set] of Object.entries(prev)) {
            const allowed = new Set(newOptions[key] || []);
            const kept = new Set([...set].filter((v) => allowed.has(v)));
            if (kept.size !== set.size) changed = true;
            next[key] = kept;
          }
          return changed ? next : prev;
        });
      })
      .catch(() => {
        if (mySeq !== optionsSeq.current) return;
        setOptionsLoading(false);
        // Keep whatever options were last successfully loaded (don't wipe a
        // working dropdown just because one refresh failed) but surface the
        // failure so it's visible instead of a silent, confusing "No matches".
        setOptionsError("Couldn't refresh filter options — showing last known values.");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain, JSON.stringify([...Object.entries(selected).map(([k, v]) => [k, [...v].sort()])]), dates.date_from, dates.date_to]);

  function toggleValue(key, value) {
    setSelected((prev) => {
      const next = { ...prev };
      const set = new Set(next[key] || []);
      if (set.has(value)) set.delete(value);
      else set.add(value);
      next[key] = set;
      return next;
    });
  }

  function selectedCount(key) {
    return (selected[key] && selected[key].size) || 0;
  }

  function buildFilters() {
    const out = {};
    for (const key of fieldNames) {
      const set = selected[key];
      if (set && set.size > 0) out[key] = [...set];
    }
    return out;
  }

  // Send a filter set to the backend and apply the response. Passing an
  // EMPTY filter set restores every KPI/chart/table to its pristine
  // `_base_sql` (the backend always re-injects from `_base_sql`, and with no
  // active filters that injection is a no-op) — this is what makes Clear
  // Filters reliable even after a page reload, when a client-side snapshot
  // of the "original" report would otherwise be stale or already filtered.
  async function sendFilters(multi, dateFrom, dateTo) {
    setError("");
    setBusy(true);
    setOpenKey(null);
    const mySeq = ++requestSeq.current;
    try {
      const body = { report, filters: multi };
      if (dateFrom) body.date_from = dateFrom;
      if (dateTo) body.date_to = dateTo;
      const res = await fetch("/report/apply-filters", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const data = await res.json();
      if (mySeq !== requestSeq.current) return;  // superseded by a newer apply/clear
      if (data && data.report) {
        onApplied(data);
      } else {
        setError("Filter returned no data. Try different values.");
      }
    } catch (e) {
      if (mySeq !== requestSeq.current) return;
      setError("Failed to apply filters. Please try again.");
    } finally {
      if (mySeq === requestSeq.current) setBusy(false);
    }
  }

  async function apply() {
    const multi = buildFilters();
    const hasDate = dates.date_from || dates.date_to;
    if (Object.keys(multi).length === 0 && !hasDate) {
      setError("Select at least one filter before applying.");
      return;
    }
    await sendFilters(multi, dates.date_from, dates.date_to);
  }

  async function clearAll() {
    setSelected({});
    setDates({ date_from: "", date_to: "" });
    // Always ask the backend to restore from _base_sql — covers both "filters
    // applied this session" and "report was already filtered before this page
    // load" (e.g. after a reload), where a client-side snapshot can't help.
    await sendFilters({}, "", "");
  }

  return (
    <div style={{
      background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 8,
      padding: "0.45rem 0.75rem", marginBottom: "0.65rem",
      display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem",
      position: "relative",
    }}>
      {applicable.date_range && (
        <InlineField label="DATE RANGE" t={t}>
          <input type="date" value={dates.date_from} onChange={(e) => setDates({ ...dates, date_from: e.target.value })} className="rpt-filter-input" style={inp(t)} />
          <span style={{ color: t.textMuted, fontSize: "0.7rem" }}>–</span>
          <input type="date" value={dates.date_to} onChange={(e) => setDates({ ...dates, date_to: e.target.value })} className="rpt-filter-input" style={inp(t)} />
        </InlineField>
      )}

      {fieldNames.map((key) => (
        <MultiSelect
          key={key}
          label={labelFor(key)}
          values={options[key] || []}
          loading={optionsLoading}
          selected={selected[key] || new Set()}
          count={selectedCount(key)}
          open={openKey === key}
          onToggleOpen={() => setOpenKey(openKey === key ? null : key)}
          onToggleValue={(v) => toggleValue(key, v)}
          onClearField={() => setSelected((p) => ({ ...p, [key]: new Set() }))}
          t={t}
        />
      ))}

      <div style={{ display: "flex", gap: "0.35rem", alignItems: "center", marginLeft: "auto" }}>
        {optionsError && (
          <span style={{ fontSize: "0.7rem", color: "#d97706", whiteSpace: "nowrap" }}>{optionsError}</span>
        )}
        {error && (
          <span style={{ fontSize: "0.7rem", color: "#ef4444", whiteSpace: "nowrap" }}>{error}</span>
        )}
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

// Excel-style multi-select: a button showing the field + count, and a dropdown
// panel of checkboxes with a search box (for long lists like products/customers).
function MultiSelect({ label, values, loading, selected, count, open, onToggleOpen, onToggleValue, onClearField, t }) {
  const [query, setQuery] = useState("");
  const filtered = query
    ? values.filter((v) => String(v).toLowerCase().includes(query.toLowerCase()))
    : values;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.3rem", position: "relative" }}>
      <span style={{ fontSize: "0.65rem", fontWeight: 700, letterSpacing: "0.06em", color: t.textMuted, whiteSpace: "nowrap" }}>{label}</span>
      <button
        onClick={onToggleOpen}
        className="rpt-filter-input"
        style={{ ...inp(t), cursor: "pointer", minWidth: 90, textAlign: "left", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.4rem" }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {count === 0 ? "All" : `${count} selected`}
        </span>
        <span style={{ fontSize: "0.6rem", color: t.textMuted }}>▼</span>
      </button>

      {open && (
        <div style={{
          position: "absolute", top: "100%", left: 0, marginTop: 4, zIndex: 50,
          background: t.bgPanel, border: `1px solid ${t.border}`, borderRadius: 6,
          boxShadow: "0 6px 20px rgba(0,0,0,0.18)", minWidth: 200, maxWidth: 320,
          maxHeight: 300, display: "flex", flexDirection: "column",
        }}>
          {values.length > 8 && (
            <input
              autoFocus
              type="text"
              placeholder="Search…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ ...inp(t), margin: 6, marginBottom: 2 }}
            />
          )}
          <div style={{ overflowY: "auto", padding: "4px 0" }}>
            {loading && (
              <div style={{ padding: "6px 12px", fontSize: "0.72rem", color: t.textMuted }}>Loading…</div>
            )}
            {!loading && filtered.length === 0 && (
              <div style={{ padding: "6px 12px", fontSize: "0.72rem", color: t.textMuted }}>No matches</div>
            )}
            {!loading && filtered.map((v) => {
              const checked = selected.has(v);
              return (
                <label
                  key={v}
                  style={{
                    display: "flex", alignItems: "center", gap: "0.5rem",
                    padding: "5px 12px", cursor: "pointer", fontSize: "0.76rem",
                    color: t.text, whiteSpace: "nowrap",
                    background: checked ? (t.bgCard || "transparent") : "transparent",
                  }}
                >
                  <input type="checkbox" checked={checked} onChange={() => onToggleValue(v)} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{String(v)}</span>
                </label>
              );
            })}
          </div>
          {count > 0 && (
            <button
              onClick={onClearField}
              style={{
                border: "none", borderTop: `1px solid ${t.border}`, background: "transparent",
                color: t.textMuted, padding: "6px", cursor: "pointer", fontSize: "0.7rem", fontWeight: 600,
              }}
            >
              Clear {label.toLowerCase()}
            </button>
          )}
        </div>
      )}
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
