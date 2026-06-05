import { useState, useEffect, useCallback, useRef } from "react";
import { getTheme } from "../theme.js";
import { formatKPIValue, formatColumnName, isCurrencyColumn, formatNum } from "./format.js";
import ChartCard from "./ChartCard.jsx";
import ExplainModal from "./ExplainModal.jsx";
import ModifyPanel from "./ModifyPanel.jsx";
import FilterBar from "./FilterBar.jsx";
import PlaceholderCard from "./PlaceholderCard.jsx";
import { exportPDF, exportExcel } from "./exporters.js";

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
      setPayload(JSON.parse(raw));
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
      {/* Topbar */}
      <div style={{ position: "sticky", top: 0, zIndex: 50, display: "flex", alignItems: "center", gap: "0.7rem",
        padding: "0.8rem 1.2rem", background: t.bgPanel, borderBottom: `1px solid ${t.border}`, backdropFilter: "blur(10px)" }}>
        <button onClick={() => window.close()} title="Close" style={iconBtn(t)}>×</button>
        <strong style={{ flex: 1, fontSize: "1rem", textAlign: "center" }}>{report.title || "Analytics Report"}</strong>
        {editMode && (
          <>
            <button onClick={() => addSlot("kpi")} style={pillBtn(t)}>+ KPI</button>
            <button onClick={() => addSlot("chart")} style={pillBtn(t)}>+ Chart</button>
          </>
        )}
        <button onClick={() => setEditMode((e) => !e)} style={editMode ? pillBtnActive(t) : pillBtn(t)}>{editMode ? "Done" : "Edit"}</button>
        <button onClick={() => exportPDF(report, chartInstances.current)} style={pillBtn(t)}>PDF</button>
        <button onClick={() => exportExcel(report)} style={pillBtn(t)}>Excel</button>
      </div>

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "1.4rem" }}>
        {loadError && (
          <div style={{ background: "#f59e0b22", border: "1px solid #f59e0b", color: t.text, borderRadius: 10, padding: "0.6rem 0.9rem", marginBottom: "1rem", fontSize: "0.82rem" }}>
            ⚠️ {loadError}
          </div>
        )}
        {/* Global filters */}
        {payload.applicable_filters && Object.keys(payload.applicable_filters).length > 0 && (
          <FilterBar applicable={payload.applicable_filters} report={report} onApplied={applyFiltered} t={t} />
        )}

        {/* Summary */}
        {report.summary && (
          <div style={{ background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 12, padding: "1rem 1.2rem", marginBottom: "1.4rem", lineHeight: 1.6, fontSize: "0.92rem" }}>
            {report.summary}
          </div>
        )}

        {/* KPIs */}
        {kpiVisible.length > 0 && (
          <>
            <SectionLabel t={t}>Key Performance Indicators</SectionLabel>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "0.8rem", marginBottom: "1.6rem" }}>
              {kpiVisible.map((k, i) => {
                const rawIdx = rawKpiIndex(i);
                if (k._placeholder) {
                  return (
                    <PlaceholderCard key={"kph" + rawIdx} phType="kpi" phIdx={rawIdx} report={report} t={t}
                      autoOpen={k._autoOpen}
                      onUpdate={persist} onRemove={() => removePlaceholder("kpi", rawIdx)} />
                  );
                }
                return (
                  <div key={i} {...dragProps("kpi", rawIdx, false)} style={{ background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 12, padding: "1rem", position: "relative", cursor: editMode ? "grab" : "default" }}>
                    <div style={{ fontSize: "0.72rem", color: t.textMuted, textTransform: "uppercase", letterSpacing: "0.03em", marginBottom: "0.4rem", paddingRight: 18 }}>{k.label}</div>
                    <div style={{ fontSize: "1.5rem", fontWeight: 700, color: k.error ? "#ef4444" : t.text }}>
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
          </>
        )}

        {/* Charts */}
        {chartVisible.length > 0 && (
          <>
            <SectionLabel t={t}>Visual Analytics</SectionLabel>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "1rem", marginBottom: "1.6rem" }}>
              {chartVisible.map((c, i) => {
                const rawIdx = rawChartIndex(i);
                if (c._placeholder) {
                  return (
                    <div key={"cph" + rawIdx} {...dragProps("chart", rawIdx, true)} style={{ gridColumn: shouldBeWide[i] ? "1 / -1" : "auto" }}>
                      <PlaceholderCard phType="chart" phIdx={rawIdx} report={report} t={t}
                        autoOpen={c._autoOpen}
                        onUpdate={persist} onRemove={() => removePlaceholder("chart", rawIdx)} />
                    </div>
                  );
                }
                return (
                  <div key={i} {...dragProps("chart", rawIdx, false)} style={{ gridColumn: shouldBeWide[i] ? "1 / -1" : "auto", cursor: editMode ? "grab" : "default", position: "relative" }}>
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
          </>
        )}

        {/* Table */}
        {table && Array.isArray(table.data) && table.data.length > 0 && (
          <>
            <SectionLabel t={t}>
              {table.title || "Detail Data"}
              {table.explanation && (
                <button onClick={() => setExplain({ title: table.title || "Detail Data", explanation: table.explanation, sql: table.sql })} style={{ ...miniEye(t), marginLeft: 8 }} title="Explain">👁</button>
              )}
            </SectionLabel>
            <DataTable rows={table.data} t={t} />
          </>
        )}

        {/* Insights */}
        {insights.length > 0 && (
          <>
            <SectionLabel t={t}>AI-Generated Insights</SectionLabel>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "0.8rem", marginBottom: "2rem" }}>
              {insights.map((ins, i) => {
                const title = typeof ins === "string" ? ins : ins.title;
                const body = typeof ins === "string" ? "" : ins.body;
                const type = (typeof ins === "object" && ins.type) || "neutral";
                const color = { positive: "#22c55e", negative: "#ef4444", warning: "#f59e0b", opportunity: "#3b82f6", neutral: t.accent }[type] || t.accent;
                return (
                  <div key={i} style={{ background: t.bgCard, border: `1px solid ${t.border}`, borderLeft: `3px solid ${color}`, borderRadius: 10, padding: "0.9rem" }}>
                    <div style={{ fontWeight: 600, fontSize: "0.85rem", marginBottom: body ? "0.35rem" : 0 }}>{title}</div>
                    {body && <div style={{ fontSize: "0.8rem", color: t.textMuted, lineHeight: 1.5 }}>{body}</div>}
                  </div>
                );
              })}
            </div>
          </>
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
    <div style={{ overflowX: "auto", border: `1px solid ${t.border}`, borderRadius: 12, marginBottom: "1.6rem" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.8rem" }}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} style={{ textAlign: "left", padding: "0.6rem 0.8rem", borderBottom: `1px solid ${t.border}`, color: t.textMuted, fontWeight: 600, position: "sticky", top: 0, background: t.bgPanel }}>
                {formatColumnName(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 200).map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} style={{ padding: "0.5rem 0.8rem", borderBottom: `1px solid ${t.border}`, color: t.text }}>
                  {isCurrencyColumn(c) && typeof r[c] === "number" ? formatNum(r[c], true) : String(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 200 && <div style={{ padding: "0.5rem 0.8rem", color: t.textMuted, fontSize: "0.74rem" }}>Showing first 200 of {rows.length} rows</div>}
    </div>
  );
}

function SectionLabel({ children, t }) {
  return <h3 style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: "0.05em", color: t.textMuted, margin: "0 0 0.7rem", fontWeight: 700 }}>{children}</h3>;
}
function EyeButton({ t, onClick }) {
  return <button onClick={onClick} title="Explain" style={{ ...miniEye(t), position: "absolute", top: 8, right: 8 }}>👁</button>;
}
function DeleteBtn({ t, onClick }) {
  return <button onClick={onClick} title="Delete" style={{ position: "absolute", top: 8, right: 8, zIndex: 25, border: "none", background: "#ef4444", color: "#fff", borderRadius: 6, width: 24, height: 24, cursor: "pointer", fontSize: "0.8rem", lineHeight: 1 }}>✕</button>;
}
function miniEye(t) {
  return { border: `1px solid ${t.border}`, background: t.bgPanel, color: t.textMuted, borderRadius: 6, width: 24, height: 24, cursor: "pointer", fontSize: "0.7rem", lineHeight: 1 };
}
function iconBtn(t) {
  return { border: `1px solid ${t.border}`, background: t.bgCard, color: t.text, width: 32, height: 32, borderRadius: 8, cursor: "pointer", fontSize: "1.1rem" };
}
function pillBtn(t) {
  return { border: `1px solid ${t.border}`, background: t.bgCard, color: t.text, borderRadius: 8, padding: "0.4rem 0.8rem", cursor: "pointer", fontSize: "0.8rem", fontWeight: 600 };
}
function pillBtnActive(t) {
  return { border: `1px solid ${t.accent}`, background: t.accent, color: "#fff", borderRadius: 8, padding: "0.4rem 0.8rem", cursor: "pointer", fontSize: "0.8rem", fontWeight: 600 };
}
function Centered({ children, t }) {
  return <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: t.bg, color: t.textMuted, fontSize: "0.95rem" }}>{children}</div>;
}
