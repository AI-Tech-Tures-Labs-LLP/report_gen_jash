import { useState, useEffect, useCallback, useRef } from "react";
import { getTheme } from "../theme.js";
import { formatKPIValue, formatColumnName, isCurrencyColumn, formatNum } from "./format.js";
import ChartCard from "./ChartCard.jsx";
import ExplainModal from "./ExplainModal.jsx";
import ModifyPanel from "./ModifyPanel.jsx";
import FilterBar from "./FilterBar.jsx";
import PlaceholderCard from "./PlaceholderCard.jsx";
import { exportPDF, exportExcel } from "./exporters.js";
import { logMetrics } from "../api.js";

export default function ReportPage() {
  const params = new URLSearchParams(window.location.search);
  const reportId = params.get("id");

  const [themeMode] = useState(
    () => (reportId && localStorage.getItem("sqlbot_report_" + reportId + "_theme")) || "light"
  );
  const t = getTheme(themeMode);

  const [payload, setPayload] = useState(null);
  const [explain, setExplain] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [editMode, setEditMode] = useState(false);

  // Live Chart.js instances keyed by chart index — for PDF image export.
  const chartInstances = useRef({});
  // Drag state for edit-mode reordering.
  const drag = useRef({ type: null, idx: -1 });
  const bcRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", themeMode);
    document.body.style.background = t.bg;
    if (!reportId) {
      setLoadError("No report specified. Generate a report from the chat to view it here.");
      return;
    }
    try {
      const raw = localStorage.getItem("sqlbot_report_" + reportId);
      if (!raw) {
        setLoadError("Report not found. It may have expired — generate it again from the chat.");
        return;
      }
      const parsed = JSON.parse(raw);
      setPayload(parsed);
      if (parsed && parsed.metrics) logMetrics(parsed.metrics, "report");
    } catch {
      setLoadError("Failed to load this report. Please generate it again from the chat.");
    }
  }, [reportId, themeMode, t.bg]);

  // ── Cross-tab sync via BroadcastChannel 'report_sync' ──────────────────
  useEffect(() => {
    try {
      const bc = new BroadcastChannel("report_sync");
      bc.onmessage = (e) => {
        const msg = e.data;
        if (msg && msg.type === "report_updated" && msg.reportId === reportId && msg.report) {
          setPayload((p) => ({ ...(p || {}), report: msg.report }));
        }
      };
      bcRef.current = bc;
      return () => { try { bc.close(); } catch { /* */ } };
    } catch { /* BroadcastChannel may be absent */ }
  }, [reportId]);

  // Persist + broadcast a report mutation.
  const persist = useCallback((newReport) => {
    setPayload((p) => {
      const next = { ...(p || {}), report: newReport };
      try { localStorage.setItem("sqlbot_report_" + reportId, JSON.stringify(next)); } catch { /* */ }
      return next;
    });
    try {
      const bc = bcRef.current || new BroadcastChannel("report_sync");
      bc.postMessage({ type: "report_updated", reportId, report: newReport });
    } catch { /* */ }
  }, [reportId]);

  const updateReport = persist;

  const applyFiltered = useCallback((newPayload) => {
    setPayload((p) => ({ ...(p || {}), ...newPayload }));
  }, []);

  const handleChartReady = useCallback((idx, instance) => {
    if (instance) chartInstances.current[idx] = instance;
    else delete chartInstances.current[idx];
  }, []);

  // Only block the whole page if there's genuinely nothing to show.
  if (!payload) {
    if (loadError) return <Centered t={t}>{loadError}</Centered>;
    return <Centered t={t}>Loading report…</Centered>;
  }
  // If we have a payload, render it; any loadError shows as a banner above it.

  const report = payload.report || {};
  const rawKpis = report.kpis || [];
  const rawCharts = report.charts || [];
  const table = report.table;
  const insights = report.insights || [];

  // KPIs shown: placeholders always; otherwise filter out empty/NaN values.
  const kpiVisible = editMode
    ? rawKpis
    : rawKpis.filter((k) => {
        if (k._placeholder) return false;
        if (k.error) return true;
        const v = k.value;
        if (v === null || v === undefined || v === "") return false;
        if (String(v).trim().toLowerCase() === "nan") return false;
        // NOTE: the backend now re-executes each KPI's SQL and OWNS the value
        // (label KPIs carry their real name, e.g. "Round"), so no special-case
        // hiding of "which/top/best" KPIs is needed — just show what the API sends.
        return true;
      });

  // Charts shown: placeholders always (edit mode); skip empty/errored/all-zero.
  const chartVisible = editMode
    ? rawCharts
    : rawCharts.filter((c) => {
        if (c._placeholder) return false;
        if (c.error) return false;
        if (!c.data || c.data.length === 0) return false;
        const keys = Object.keys(c.data[0]);
        if (keys.length < 2) return false;
        const valueKeys = keys.slice(1);
        const allZero = c.data.every((row) => valueKeys.every((k) => { const v = Number(row[k]); return isNaN(v) || v === 0; }));
        return !allZero;
      });

  // ── Wide-chart placement (faithful port of shouldBeWide logic) ──────────
  const naturallyWide = chartVisible.map((c) =>
    !c._placeholder && ["line", "area", "stackedbar"].includes((c.type || "bar").toLowerCase())
  );
  const shouldBeWide = [...naturallyWide];
  {
    let col = 0;
    for (let i = 0; i < chartVisible.length; i++) {
      if (shouldBeWide[i]) { col = 0; }
      else if (col === 0) {
        const nextWide = (i + 1 >= chartVisible.length) || shouldBeWide[i + 1];
        if (nextWide) { shouldBeWide[i] = true; col = 0; } else { col = 1; }
      } else { col = 0; }
    }
  }

  // Map a visible-index back to the raw array index (for mutating placeholders).
  const rawKpiIndex = (visIdx) => rawKpis.indexOf(kpiVisible[visIdx]);
  const rawChartIndex = (visIdx) => rawCharts.indexOf(chartVisible[visIdx]);

  // ── Edit-mode mutations ────────────────────────────────────────────────
  function deleteComponent(type, rawIdx) {
    const next = JSON.parse(JSON.stringify(report));
    const arr = type === "kpi" ? next.kpis : next.charts;
    if (arr && rawIdx >= 0 && rawIdx < arr.length) arr[rawIdx] = { _placeholder: true };
    persist(next);
  }
  function removePlaceholder(type, rawIdx) {
    const next = JSON.parse(JSON.stringify(report));
    const arr = type === "kpi" ? next.kpis : next.charts;
    if (arr && rawIdx >= 0) arr.splice(rawIdx, 1);
    persist(next);
  }
  function addSlot(type) {
    const next = JSON.parse(JSON.stringify(report));
    if (type === "kpi") { next.kpis = next.kpis || []; next.kpis.push({ _placeholder: true, _autoOpen: true }); }
    else { next.charts = next.charts || []; next.charts.push({ _placeholder: true, _autoOpen: true }); }
    persist(next);
  }
  function onDrop(type, dstRawIdx, dstIsPlaceholder) {
    const d = drag.current;
    if (d.type !== type || d.idx < 0 || d.idx === dstRawIdx) return;
    const next = JSON.parse(JSON.stringify(report));
    const arr = type === "kpi" ? next.kpis : next.charts;
    if (!arr) return;
    if (dstIsPlaceholder) {
      const [item] = arr.splice(d.idx, 1);
      const adjustedDst = d.idx < dstRawIdx ? dstRawIdx - 1 : dstRawIdx;
      arr[adjustedDst] = item;
    } else {
      const [item] = arr.splice(d.idx, 1);
      arr.splice(dstRawIdx, 0, item);
    }
    drag.current = { type: null, idx: -1 };
    persist(next);
  }

  const dragProps = (type, rawIdx, isPlaceholder) => editMode ? {
    draggable: !isPlaceholder,
    onDragStart: (e) => { drag.current = { type, idx: rawIdx }; e.dataTransfer.effectAllowed = "move"; },
    onDragOver: (e) => { if (drag.current.type === type) e.preventDefault(); },
    onDrop: (e) => { e.preventDefault(); e.stopPropagation(); onDrop(type, rawIdx, isPlaceholder); },
    onDragEnd: () => { drag.current = { type: null, idx: -1 }; },
  } : {};

  return (
    <div style={{ minHeight: "100vh", background: t.bg, color: t.text }}>
      {/* ── Topbar ── */}
      <div style={{
        position: "sticky", top: 0, zIndex: 50,
        display: "flex", alignItems: "center", gap: "0.5rem",
        padding: "0 1.25rem", height: 44,
        background: t.bgPanel, borderBottom: `1px solid ${t.border}`,
        backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)",
      }}>
        <button onClick={() => window.close()} className="rpt-close-btn" style={closeLinkBtn(t)}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ flexShrink: 0 }}>
            <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          Close Report
        </button>
        <div style={{ width: 1, height: 18, background: t.border, flexShrink: 0 }} />
        <span style={{ flex: 1, fontSize: "0.875rem", fontWeight: 600, color: t.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {report.title || "Analytics Report"}
        </span>
        {editMode && (
          <>
            <button onClick={() => addSlot("kpi")} className="rpt-toolbar-btn" style={topBtn(t)}>+ KPI</button>
            <button onClick={() => addSlot("chart")} className="rpt-toolbar-btn" style={topBtn(t)}>+ Chart</button>
          </>
        )}
        <button onClick={() => setEditMode((e) => !e)} className="rpt-toolbar-btn"
          style={editMode ? topBtnActive(t) : topBtn(t)}>
          {editMode ? "✓ Done" : "Edit"}
        </button>
        <div style={{ width: 1, height: 18, background: t.border, flexShrink: 0 }} />
        <button onClick={() => exportPDF(report, chartInstances.current)} className="rpt-toolbar-btn" style={topBtn(t)}>
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ marginRight: 4 }}>
            <path d="M6.5 1v8M3 6l3.5 3.5L10 6M1 12h11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          PDF
        </button>
        <button onClick={() => exportExcel(report)} className="rpt-toolbar-btn" style={topBtn(t)}>
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ marginRight: 4 }}>
            <path d="M1 12h11M1 1h11M4 1v11M9 1v11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          Excel
        </button>
      </div>

      {/* ── Page body ── */}
      <div style={{ maxWidth: 1450, margin: "0 auto", padding: "0.5rem 1.5rem 2rem" }}>

        {loadError && (
          <div style={{ background: "#fef3c7", border: "1px solid #fbbf24", color: "#92400e", borderRadius: 8, padding: "0.55rem 0.85rem", marginBottom: "0.85rem", fontSize: "0.8rem", display: "flex", gap: "0.4rem", alignItems: "center" }}>
            <span>⚠</span> {loadError}
          </div>
        )}

        {/* Global filters — inline row ABOVE summary */}
        {payload.applicable_filters && Object.keys(payload.applicable_filters).length > 0 && (
          <FilterBar applicable={payload.applicable_filters} report={report} onApplied={applyFiltered} t={t} />
        )}

        {/* Summary — bullet points */}
        {report.summary && (
          <div className="rpt-section" style={{
            background: "rgba(99,102,241,0.03)",
            border: `1px solid ${t.border}`,
            borderLeft: "4px solid #6366f1",
            borderRadius: 12,
            padding: "1rem 1.25rem",
            marginBottom: "0.75rem",
            boxShadow: "0 1px 4px rgba(0,0,0,0.02)"
          }}>
            <SummaryBullets text={report.summary} t={t} />
          </div>
        )}

        {/* KPIs */}
        {kpiVisible.length > 0 && (
          <div className="rpt-section" style={{ marginBottom: "0.75rem" }}>
            <SectionLabel t={t} icon={<KpiIcon />}>Key Performance Indicators</SectionLabel>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "0.6rem", marginBottom: 0 }}>
              {kpiVisible.map((k, i) => {
                const rawIdx = rawKpiIndex(i);
                const kpiGradients = [
                  "linear-gradient(135deg, #d4af37 0%, #b8860b 50%, #8b6914 100%)",
                  "linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)",
                  "linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%)",
                  "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)",
                  "linear-gradient(135deg, #06b6d4 0%, #0891b2 100%)",
                  "linear-gradient(135deg, #f43f5e 0%, #e11d48 100%)"
                ];
                const gradient = kpiGradients[i % kpiGradients.length];
                if (k._placeholder) {
                  return (
                    <PlaceholderCard key={"kph" + rawIdx} phType="kpi" phIdx={rawIdx} report={report} t={t}
                      autoOpen={k._autoOpen} onUpdate={persist} onRemove={() => removePlaceholder("kpi", rawIdx)} />
                  );
                }
                return (
                  <div key={i} className="rpt-kpi-card"
                    {...dragProps("kpi", rawIdx, false)}
                    style={{
                      background: t.bgCard, border: `1px solid ${t.border}`,
                      borderRadius: 10, overflow: "hidden",
                      padding: "1.1rem 1rem 0.85rem", position: "relative",
                      cursor: editMode ? "grab" : "default",
                    }}>
                    <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: gradient }} />
                    <div style={{ fontSize: "0.64rem", color: t.textMuted, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: "0.45rem", lineHeight: 1.3, paddingRight: k.explanation ? 22 : 0 }}>{k.label}</div>
                    <div style={{ fontSize: "1.6rem", fontWeight: 700, color: k.error ? "#ef4444" : t.text, lineHeight: 1.1, letterSpacing: "-0.02em" }}>
                      {k.error ? "Error" : formatKPIValue(k.value, k.format)}
                    </div>
                    {!editMode && k.explanation && (
                      <EyeButton t={t} onClick={() => setExplain({ title: k.label, explanation: k.explanation, sql: k.sql })} />
                    )}
                    {editMode && <DeleteBtn t={t} onClick={() => deleteComponent("kpi", rawIdx)} />}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Charts */}
        {chartVisible.length > 0 && (
          <div className="rpt-section" style={{ marginBottom: "0.75rem" }}>
            <SectionLabel t={t} icon={<ChartIcon />}>Visual Analytics</SectionLabel>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "0.6rem", marginBottom: 0 }}>
              {chartVisible.map((c, i) => {
                const rawIdx = rawChartIndex(i);
                if (c._placeholder) {
                  return (
                    <div key={"cph" + rawIdx} {...dragProps("chart", rawIdx, true)} style={{ gridColumn: shouldBeWide[i] ? "1 / -1" : "auto" }}>
                      <PlaceholderCard phType="chart" phIdx={rawIdx} report={report} t={t}
                        autoOpen={c._autoOpen} onUpdate={persist} onRemove={() => removePlaceholder("chart", rawIdx)} />
                    </div>
                  );
                }
                return (
                  <div key={i} {...dragProps("chart", rawIdx, false)}
                    style={{ gridColumn: shouldBeWide[i] ? "1 / -1" : "auto", cursor: editMode ? "grab" : "default", position: "relative" }}>
                    <ChartCard
                      spec={c} chartIdx={rawIdx} themeMode={themeMode} t={t} wide={shouldBeWide[i]}
                      report={report} onUpdateReport={persist}
                      onExplain={setExplain} onChartReady={handleChartReady} editMode={editMode}
                    />
                    {editMode && <DeleteBtn t={t} onClick={() => deleteComponent("chart", rawIdx)} />}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Table */}
        {table && Array.isArray(table.data) && table.data.length > 0 && (
          <div className="rpt-section" style={{ marginBottom: "0.75rem" }}>
            <SectionLabel t={t} icon={<TableIcon />}>
              {table.title || "Detail Data"}
              {table.explanation && (
                <button onClick={() => setExplain({ title: table.title || "Detail Data", explanation: table.explanation, sql: table.sql })}
                  style={{ marginLeft: 8, background: "none", border: "none", cursor: "pointer", color: t.textMuted, display: "inline-flex", alignItems: "center" }} title="Explain">
                  <EyeSvg size={13} color={t.textMuted} />
                </button>
              )}
            </SectionLabel>
            <DataTable rows={table.data} t={t} />
          </div>
        )}

        {/* Insights */}
        {insights.length > 0 && (
          <div className="rpt-section" style={{ marginBottom: "0.75rem" }}>
            <SectionLabel t={t} icon={<InsightIcon />}>AI-Generated Insights</SectionLabel>
            <div className="rpt-insights-grid" style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: "0.65rem",
              marginBottom: 0
            }}>
              {insights.map((ins, i) => {
                const title = typeof ins === "string" ? ins : ins.title;
                const body = typeof ins === "string" ? "" : ins.body;
                const type = (typeof ins === "object" && ins.type) || "neutral";
                const typeMap = {
                  positive:    { color: "#10b981", bg: "rgba(16,185,129,0.08)", label: "POSITIVE" },
                  negative:    { color: "#ef4444", bg: "rgba(239,68,68,0.08)", label: "NEGATIVE" },
                  warning:     { color: "#f59e0b", bg: "rgba(245,158,11,0.08)", label: "WARNING" },
                  opportunity: { color: "#3b82f6", bg: "rgba(59,130,246,0.08)", label: "OPPORTUNITY" },
                  neutral:     { color: "#6366f1", bg: "rgba(99,102,241,0.08)", label: "NEUTRAL" },
                };
                const tm = typeMap[type] || typeMap.neutral;
                return (
                  <div key={i} className="rpt-insight-card"
                    style={{
                      background: t.bgCard,
                      border: `1px solid ${t.border}`,
                      borderLeft: `4px solid ${tm.color}`,
                      borderRadius: 12,
                      padding: "1rem 1.1rem",
                    }}>
                    <div style={{ marginBottom: "0.55rem" }}>
                      <span style={{ fontSize: "0.58rem", fontWeight: 700, letterSpacing: "0.06em", color: tm.color, background: tm.bg, borderRadius: 4, padding: "0.15rem 0.45rem", textTransform: "uppercase" }}>{tm.label}</span>
                    </div>
                    <div style={{ fontWeight: 700, fontSize: "0.86rem", marginBottom: body ? "0.45rem" : 0, color: t.text, lineHeight: 1.45 }}>{title}</div>
                    {body && <div style={{ fontSize: "0.775rem", color: t.textMuted, lineHeight: 1.6 }}>{body}</div>}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {explain && <ExplainModal {...explain} t={t} onClose={() => setExplain(null)} />}
      <ModifyPanel report={report} onUpdate={updateReport} t={t} />
    </div>
  );
}

function DataTable({ rows, t }) {
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return (
    <div style={{ overflowX: "auto", border: `1px solid ${t.border}`, borderRadius: 10, marginBottom: "1.25rem", boxShadow: "0 1px 4px rgba(0,0,0,0.04)" }}>
      <table className="rpt-table" style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.79rem" }}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} style={{ textAlign: "left", padding: "0.55rem 0.85rem", borderBottom: `1px solid ${t.border}`, color: t.textMuted, fontWeight: 600, fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.05em", position: "sticky", top: 0, background: t.bgPanel, whiteSpace: "nowrap" }}>
                {formatColumnName(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 200).map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} style={{ padding: "0.48rem 0.85rem", borderBottom: `1px solid ${t.border}`, color: t.text, background: t.bgCard }}>
                  {isCurrencyColumn(c) && typeof r[c] === "number" ? formatNum(r[c], true) : String(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 200 && (
        <div style={{ padding: "0.45rem 0.85rem", color: t.textMuted, fontSize: "0.72rem", background: t.bgCard, borderTop: `1px solid ${t.border}` }}>
          Showing first 200 of {rows.length} rows
        </div>
      )}
    </div>
  );
}

function EyeSvg({ size = 14, color = "currentColor" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none">
      <ellipse cx="8" cy="8" rx="7" ry="4.5" stroke={color} strokeWidth="1.4"/>
      <circle cx="8" cy="8" r="2" fill={color}/>
    </svg>
  );
}
function EyeButton({ t, onClick }) {
  return (
    <button onClick={onClick} title="Explain"
      style={{ position: "absolute", top: 8, right: 8, border: `1px solid ${t.border}`, background: t.bgPanel, color: t.textMuted, borderRadius: 6, width: 24, height: 24, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", padding: 0 }}>
      <EyeSvg size={13} color={t.textMuted} />
    </button>
  );
}
function DeleteBtn({ t, onClick }) {
  return (
    <button onClick={onClick} title="Delete"
      style={{ position: "absolute", top: 8, right: 8, zIndex: 25, border: "none", background: "#ef4444", color: "#fff", borderRadius: 6, width: 22, height: 22, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.75rem", lineHeight: 1 }}>
      ✕
    </button>
  );
}
function SectionLabel({ children, t, icon }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.4rem" }}>
      {icon && <span style={{ color: t.accent, display: "flex", alignItems: "center" }}>{icon}</span>}
      <h3 style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: "0.08em", color: t.textMuted, fontWeight: 700, margin: 0 }}>
        {children}
      </h3>
    </div>
  );
}
function SummaryBullets({ text, t }) {
  const raw = (text || "").trim();
  let bullets;
  if (raw.includes("\n")) {
    bullets = raw.split("\n").map((l) => l.trim()).filter(Boolean);
  } else {
    bullets = raw.split(/(?<=\.)\s+(?=[A-Z])/).filter(Boolean);
  }
  if (bullets.length <= 1) {
    return <p style={{ fontSize: "0.855rem", lineHeight: 1.75, margin: 0, color: t.text }}>{text}</p>;
  }
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "0.55rem" }}>
      {bullets.map((b, i) => (
        <li key={i} style={{ display: "flex", gap: "0.65rem", alignItems: "flex-start", fontSize: "0.855rem", lineHeight: 1.7, color: t.text }}>
          <svg width="8" height="8" viewBox="0 0 8 8" style={{ flexShrink: 0, marginTop: "0.45rem" }}>
            <polygon points="0,0 8,4 0,8" fill="#6366f1" />
          </svg>
          <span>{b.replace(/^[-•▶►]\s*/, "")}</span>
        </li>
      ))}
    </ul>
  );
}
function KpiIcon() {
  return <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><rect x="1" y="7" width="2.5" height="5" rx="1" fill="currentColor"/><rect x="5" y="4" width="2.5" height="8" rx="1" fill="currentColor"/><rect x="9.5" y="1" width="2.5" height="11" rx="1" fill="currentColor"/></svg>;
}
function ChartIcon() {
  return <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><polyline points="1,10 4,6 7,8 10,3 12,5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" fill="none"/></svg>;
}
function TableIcon() {
  return <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><rect x="1" y="1" width="11" height="11" rx="2" stroke="currentColor" strokeWidth="1.3"/><line x1="1" y1="5" x2="12" y2="5" stroke="currentColor" strokeWidth="1.2"/><line x1="5" y1="5" x2="5" y2="12" stroke="currentColor" strokeWidth="1.2"/></svg>;
}
function InsightIcon() {
  return <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M6.5 1a4 4 0 0 1 1.5 7.7V10H5V8.7A4 4 0 0 1 6.5 1z" stroke="currentColor" strokeWidth="1.3" fill="none"/><line x1="5" y1="11" x2="8" y2="11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>;
}
function closeLinkBtn(t) {
  return { border: "none", background: "transparent", color: t.textMuted, cursor: "pointer", fontSize: "0.78rem", fontWeight: 500, padding: "0 0.5rem", borderRadius: 6, display: "flex", alignItems: "center", gap: "0.3rem", height: "100%", whiteSpace: "nowrap" };
}
function topBtn(t) {
  return { border: `1px solid ${t.border}`, background: t.bgCard, color: t.text, borderRadius: 7, padding: "0.3rem 0.7rem", cursor: "pointer", fontSize: "0.775rem", fontWeight: 500, display: "inline-flex", alignItems: "center", height: 30, whiteSpace: "nowrap" };
}
function topBtnActive(t) {
  return { border: `1px solid ${t.accent}`, background: t.accent, color: "#fff", borderRadius: 7, padding: "0.3rem 0.7rem", cursor: "pointer", fontSize: "0.775rem", fontWeight: 600, display: "inline-flex", alignItems: "center", height: 30, whiteSpace: "nowrap" };
}
function Centered({ children, t }) {
  return <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: t.bg, color: t.textMuted, fontSize: "0.95rem" }}>{children}</div>;
}
