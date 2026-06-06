import { useState, useRef, useEffect } from "react";
import ChartView from "./ChartView.jsx";
import ChartFilterDrawer from "./ChartFilterDrawer.jsx";
import ChartAiPanel from "./ChartAiPanel.jsx";
import { downloadChartPNG, downloadChartPDF } from "./exporters.js";

// One chart card: title + toolbar (explain / AI modify / download / filter),
// the chart itself, plus the per-chart filter drawer and AI panel.
export default function ChartCard({
  spec, chartIdx, themeMode, t, wide,
  report, onUpdateReport, onExplain, onChartReady, editMode,
}) {
  // Filtered data drives ChartView; null = show original spec.data.
  const [filteredData, setFilteredData] = useState(null);
  const [chips, setChips] = useState([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [dlOpen, setDlOpen] = useState(false);
  const dlRef = useRef(null);
  const instRef = useRef(null);

  // Original data is captured once from the spec so Reset always works even
  // after filtering. When the spec.data identity changes (modify/re-render),
  // drop any active filter.
  useEffect(() => { setFilteredData(null); setChips([]); }, [spec.data]);

  useEffect(() => {
    function onDocClick(e) { if (dlRef.current && !dlRef.current.contains(e.target)) setDlOpen(false); }
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, []);

  function handleReady(instance) {
    instRef.current = instance;
    if (onChartReady) onChartReady(chartIdx, instance);
  }

  const title = spec.title || "Chart " + (chartIdx + 1);

  return (
    <div className="rpt-chart-card" style={{
      background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 10,
      padding: "0.85rem 0.9rem 0.75rem", position: "relative",
      gridColumn: wide ? "1 / -1" : "auto",
      opacity: editMode ? 0.96 : 1,
      boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
    }}>
      {/* ── Card header ── */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.65rem" }}>
        <span style={{ fontSize: "0.84rem", fontWeight: 600, color: t.text, flex: 1, lineHeight: 1.3 }}>{title}</span>
        {chips.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.2rem" }}>
            {chips.map((c, i) => (
              <span key={i} style={{ fontSize: "0.6rem", background: `${t.accent}18`, border: `1px solid ${t.accent}44`, color: t.accent, borderRadius: 20, padding: "0.05rem 0.45rem", fontWeight: 500 }}>{c}</span>
            ))}
          </div>
        )}
        {/* Toolbar */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.2rem", flexShrink: 0 }}>
          {spec.explanation && (
            <ChartToolBtn title="Explain" active={false} t={t} onClick={() => onExplain({ title, explanation: spec.explanation, sql: spec.sql })}>
              <EyeIcon />
            </ChartToolBtn>
          )}
          <ChartToolBtn title="AI Modify" active={aiOpen} t={t} onClick={() => { setAiOpen((o) => !o); setDrawerOpen(false); }}>
            <EditIcon />
          </ChartToolBtn>
          <div ref={dlRef} style={{ position: "relative" }}>
            <ChartToolBtn title="Download" active={dlOpen} t={t} onClick={(e) => { e.stopPropagation(); setDlOpen((o) => !o); }}>
              <DownloadIcon />
            </ChartToolBtn>
            {dlOpen && (
              <div style={{ position: "absolute", top: 32, right: 0, zIndex: 30, background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 8, boxShadow: "0 8px 24px rgba(0,0,0,0.12)", overflow: "hidden", minWidth: 140 }}>
                <button onClick={() => { setDlOpen(false); downloadChartPNG(instRef.current, title); }} style={dlOpt(t)}>PNG Image</button>
                <button onClick={() => { setDlOpen(false); downloadChartPDF(instRef.current, title); }} style={dlOpt(t)}>PDF Document</button>
              </div>
            )}
          </div>
          <ChartToolBtn title="Filter chart" active={drawerOpen || chips.length > 0} t={t} onClick={() => { setDrawerOpen((o) => !o); setAiOpen(false); }}>
            <FilterIcon />
            {chips.length > 0 && (
              <span style={{ position: "absolute", top: -4, right: -4, background: t.accent, color: "#fff", borderRadius: "50%", fontSize: "0.55rem", minWidth: 14, height: 14, lineHeight: "14px", textAlign: "center", padding: "0 2px", fontWeight: 700 }}>{chips.length}</span>
            )}
          </ChartToolBtn>
        </div>
      </div>

      {drawerOpen && (
        <ChartFilterDrawer
          spec={spec}
          originalData={spec.data}
          t={t}
          onApply={(data, desc) => { setFilteredData(data); setChips(desc); setDrawerOpen(false); }}
          onReset={() => { setFilteredData(null); setChips([]); }}
          onClose={() => setDrawerOpen(false)}
        />
      )}

      <ChartView
        spec={spec}
        data={filteredData}
        theme={themeMode}
        chartIdx={chartIdx}
        wide={wide}
        onReady={handleReady}
      />

      {aiOpen && (
        <ChartAiPanel
          chartIdx={chartIdx}
          report={report}
          t={t}
          onClose={() => setAiOpen(false)}
          onUpdate={(newReport) => { onUpdateReport(newReport); setAiOpen(false); }}
        />
      )}
    </div>
  );
}

function ChartToolBtn({ t, active, title, onClick, children }) {
  return (
    <button title={title} onClick={onClick} style={{
      position: "relative", border: `1px solid ${active ? t.accent : t.border}`,
      background: active ? `${t.accent}18` : t.bgPanel,
      color: active ? t.accent : t.textMuted,
      borderRadius: 6, width: 28, height: 28, cursor: "pointer",
      display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
      transition: "background 0.15s, color 0.15s, border-color 0.15s",
    }}>
      {children}
    </button>
  );
}
function EyeIcon() {
  return <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><ellipse cx="8" cy="8" rx="7" ry="4.5" stroke="currentColor" strokeWidth="1.4"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>;
}
function EditIcon() {
  return <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M9 2l2 2-7 7H2V9l7-7z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}
function DownloadIcon() {
  return <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M6.5 1v8M3.5 6.5l3 3 3-3M1.5 11.5h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}
function FilterIcon() {
  return <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><path d="M1 2.5h11M3 6.5h7M5 10.5h3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>;
}
function dlOpt(t) {
  return { display: "block", width: "100%", textAlign: "left", border: "none", background: "transparent", color: t.text, padding: "0.5rem 0.85rem", cursor: "pointer", fontSize: "0.775rem", fontWeight: 500 };
}
