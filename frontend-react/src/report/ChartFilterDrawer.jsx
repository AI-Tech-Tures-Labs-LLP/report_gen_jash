import { useMemo, useState } from "react";
import { formatColumnName, isPieType } from "./format.js";

// Per-chart filter drawer. Faithful port of buildChartFilterPanel /
// applyChartFilter / resetChartFilter from report.js.
// Computes filtered+sorted+topN data from the chart's ORIGINAL data and reports
// it (plus active-filter descriptions) back to the parent via onApply / onReset.
export default function ChartFilterDrawer({ spec, originalData, onApply, onReset, onClose, t }) {
  const data = originalData || [];
  const keys = data.length ? Object.keys(data[0]) : [];
  const labelKey = keys[0];
  const valueKeys = keys.slice(1);
  const chartType = (spec.type || "bar").toLowerCase();

  const uniqueLabels = useMemo(
    () => [...new Set(data.map((r) => String(r[labelKey])))],
    [data, labelKey]
  );
  const totalItems = data.length;

  const isPie = isPieType(chartType);
  const isLine = chartType === "line" || chartType === "area";
  const isBar = chartType === "bar" || chartType === "horizontalbar" || chartType === "stackedbar";
  const isScatter = chartType === "scatter" || chartType === "bubble";

  const looksLikeDate =
    /date|week|month|year|period|time/i.test(labelKey) ||
    (uniqueLabels.length > 0 && /^\d{4}[-/]\d{2}/.test(String(uniqueLabels[0])));

  const showDateRange = isLine && looksLikeDate && uniqueLabels.length > 3;
  const showLabelPills = !looksLikeDate && uniqueLabels.length > 1 && uniqueLabels.length <= 12;
  const showLabelSelect = !looksLikeDate && !showLabelPills && uniqueLabels.length <= 50 && uniqueLabels.length > 1;
  const showLabelSearch = !looksLikeDate && uniqueLabels.length > 50;
  const showTopN = totalItems > 4 && !isLine && !isScatter;
  const showSort = isBar || isPie;
  const showSeries = valueKeys.length > 1;
  const showGroupOthers = showTopN && totalItems > 6;
  const topOptions = [5, 10, 15, 20].filter((n) => n < totalItems);

  // ── Control state ───────────────────────────────────────────────────────
  const [selectedPills, setSelectedPills] = useState([]); // [] = All
  const [labelSelect, setLabelSelect] = useState("__all__");
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sort, setSort] = useState("default");
  const [topN, setTopN] = useState(0);
  const [groupOthers, setGroupOthers] = useState(false);
  const [series, setSeries] = useState("__all__");

  function togglePill(val) {
    if (val === "__all__") { setSelectedPills([]); return; }
    setSelectedPills((prev) =>
      prev.includes(val) ? prev.filter((p) => p !== val) : [...prev, val]
    );
  }

  function compute() {
    const src = JSON.parse(JSON.stringify(data));
    const primaryValueKey = valueKeys[0];
    let filtered = [...src];
    const desc = [];

    // 1. Label filter
    if (showLabelPills && selectedPills.length > 0) {
      filtered = filtered.filter((row) => selectedPills.includes(String(row[labelKey])));
      desc.push(`${selectedPills.length} selected`);
    }
    if (showLabelSelect && labelSelect !== "__all__") {
      filtered = filtered.filter((row) => String(row[labelKey]) === labelSelect);
      desc.push(labelSelect);
    }
    if (showLabelSearch && search.trim()) {
      const term = search.trim().toLowerCase();
      filtered = filtered.filter((row) => String(row[labelKey]).toLowerCase().includes(term));
      desc.push(`"${search.trim()}"`);
    }
    if (showDateRange && dateFrom) {
      filtered = filtered.filter((row) => String(row[labelKey]) >= dateFrom);
      desc.push(`From ${dateFrom}`);
    }
    if (showDateRange && dateTo) {
      filtered = filtered.filter((row) => String(row[labelKey]) <= dateTo);
      desc.push(`To ${dateTo}`);
    }

    // 2. Sort
    if (showSort && sort !== "default") {
      if (sort === "desc") { filtered.sort((a, b) => (Number(b[primaryValueKey]) || 0) - (Number(a[primaryValueKey]) || 0)); desc.push("High→Low"); }
      else if (sort === "asc") { filtered.sort((a, b) => (Number(a[primaryValueKey]) || 0) - (Number(b[primaryValueKey]) || 0)); desc.push("Low→High"); }
      else if (sort === "alpha") { filtered.sort((a, b) => String(a[labelKey]).localeCompare(String(b[labelKey]))); desc.push("A→Z"); }
      else if (sort === "alpha_desc") { filtered.sort((a, b) => String(b[labelKey]).localeCompare(String(a[labelKey]))); desc.push("Z→A"); }
    }

    // 3. Top N + group others
    if (showTopN && topN > 0) {
      const n = topN;
      if (sort === "default") {
        filtered.sort((a, b) => (Number(b[primaryValueKey]) || 0) - (Number(a[primaryValueKey]) || 0));
      }
      if (groupOthers && filtered.length > n) {
        const top = filtered.slice(0, n);
        const rest = filtered.slice(n);
        const othersRow = { [labelKey]: `Others (${rest.length})` };
        valueKeys.forEach((k) => { othersRow[k] = rest.reduce((s, r) => s + (Number(r[k]) || 0), 0); });
        filtered = [...top, othersRow];
        desc.push(`Top ${n} + Others`);
      } else {
        filtered = filtered.slice(0, n);
        desc.push(`Top ${n}`);
      }
    }

    // 4. Series selector — drop other value columns from the rows
    if (showSeries && series !== "__all__") {
      filtered = filtered.map((row) => ({ [labelKey]: row[labelKey], [series]: row[series] }));
      desc.push(formatColumnName(series));
    }

    return { filtered, desc };
  }

  function handleApply() {
    const { filtered, desc } = compute();
    onApply(filtered, desc);
  }

  function handleReset() {
    setSelectedPills([]); setLabelSelect("__all__"); setSearch("");
    setDateFrom(""); setDateTo(""); setSort("default"); setTopN(0);
    setGroupOthers(false); setSeries("__all__");
    onReset();
  }

  if (keys.length < 2) return null;

  return (
    <div style={{
      position: "absolute", top: 44, right: 8, zIndex: 30, width: 250,
      background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 12,
      boxShadow: t.shadow, backdropFilter: "blur(16px)", overflow: "hidden",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0.6rem 0.8rem", borderBottom: `1px solid ${t.border}` }}>
        <strong style={{ fontSize: "0.78rem", color: t.text }}>Filters</strong>
        <button onClick={onClose} style={{ border: "none", background: "transparent", color: t.textMuted, cursor: "pointer", fontSize: "1rem", lineHeight: 1 }}>×</button>
      </div>

      <div style={{ padding: "0.7rem 0.8rem", maxHeight: 360, overflowY: "auto", display: "flex", flexDirection: "column", gap: "0.7rem" }}>
        {showDateRange && (
          <Section label="Date Range" t={t}>
            <div style={{ display: "flex", gap: "0.3rem", alignItems: "center" }}>
              <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} style={inp(t)} />
              <span style={{ color: t.textMuted, fontSize: "0.7rem" }}>→</span>
              <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} style={inp(t)} />
            </div>
          </Section>
        )}

        {showLabelPills && (
          <Section label={formatColumnName(labelKey)} t={t}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
              <Pill active={selectedPills.length === 0} onClick={() => togglePill("__all__")} t={t}>All</Pill>
              {uniqueLabels.map((l) => (
                <Pill key={l} active={selectedPills.includes(l)} onClick={() => togglePill(l)} t={t}>
                  {l.length > 18 ? l.substring(0, 16) + "…" : l}
                </Pill>
              ))}
            </div>
          </Section>
        )}

        {showLabelSelect && (
          <Section label={formatColumnName(labelKey)} t={t}>
            <select value={labelSelect} onChange={(e) => setLabelSelect(e.target.value)} style={sel(t)}>
              <option value="__all__">All {formatColumnName(labelKey)}s</option>
              {uniqueLabels.map((l) => (
                <option key={l} value={l}>{l.length > 32 ? l.substring(0, 30) + "…" : l}</option>
              ))}
            </select>
          </Section>
        )}

        {showLabelSearch && (
          <Section label={formatColumnName(labelKey)} t={t}>
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder={`Search ${formatColumnName(labelKey).toLowerCase()}…`} style={inp(t)} />
          </Section>
        )}

        {showSeries && (
          <Section label="Series" t={t}>
            <select value={series} onChange={(e) => setSeries(e.target.value)} style={sel(t)}>
              <option value="__all__">All Series</option>
              {valueKeys.map((k) => <option key={k} value={k}>{formatColumnName(k)}</option>)}
            </select>
          </Section>
        )}

        {showSort && (
          <Section label="Sort" t={t}>
            <select value={sort} onChange={(e) => setSort(e.target.value)} style={sel(t)}>
              <option value="default">Default</option>
              <option value="desc">↓ Highest first</option>
              <option value="asc">↑ Lowest first</option>
              <option value="alpha">A → Z</option>
              <option value="alpha_desc">Z → A</option>
            </select>
          </Section>
        )}

        {showTopN && topOptions.length > 0 && (
          <Section label="Show Top" t={t}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
              <Pill active={topN === 0} onClick={() => setTopN(0)} t={t}>All</Pill>
              {topOptions.map((n) => (
                <Pill key={n} active={topN === n} onClick={() => setTopN(n)} t={t}>Top {n}</Pill>
              ))}
            </div>
            {showGroupOthers && (
              <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginTop: "0.5rem", fontSize: "0.74rem", color: t.text, cursor: "pointer" }}>
                <input type="checkbox" checked={groupOthers} onChange={(e) => setGroupOthers(e.target.checked)} />
                Group rest as &quot;Others&quot;
              </label>
            )}
          </Section>
        )}
      </div>

      <div style={{ display: "flex", gap: "0.4rem", padding: "0.6rem 0.8rem", borderTop: `1px solid ${t.border}` }}>
        <button onClick={handleApply} style={{ flex: 1, border: "none", borderRadius: 8, padding: "0.4rem", cursor: "pointer", color: "#fff", background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`, fontSize: "0.76rem", fontWeight: 600 }}>Apply</button>
        <button onClick={handleReset} style={{ border: `1px solid ${t.border}`, background: t.bgPanel, color: t.text, borderRadius: 8, padding: "0.4rem 0.7rem", cursor: "pointer", fontSize: "0.76rem" }}>Reset</button>
      </div>
    </div>
  );
}

function Section({ label, children, t }) {
  return (
    <div>
      <div style={{ fontSize: "0.66rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: t.textMuted, marginBottom: "0.35rem" }}>{label}</div>
      {children}
    </div>
  );
}
function Pill({ active, onClick, children, t }) {
  return (
    <button onClick={onClick} style={{
      border: `1px solid ${active ? t.accent : t.border}`,
      background: active ? t.accent : t.bgPanel,
      color: active ? "#fff" : t.text,
      borderRadius: 12, padding: "0.2rem 0.55rem", fontSize: "0.7rem", cursor: "pointer", fontWeight: 500,
    }}>{children}</button>
  );
}
function inp(t) {
  return { flex: 1, minWidth: 0, border: `1px solid ${t.border}`, background: t.bgPanel, color: t.text, borderRadius: 6, padding: "0.3rem 0.45rem", fontSize: "0.74rem", outline: "none", width: "100%" };
}
function sel(t) {
  return { width: "100%", border: `1px solid ${t.border}`, background: t.bgPanel, color: t.text, borderRadius: 6, padding: "0.3rem 0.45rem", fontSize: "0.74rem", outline: "none" };
}
