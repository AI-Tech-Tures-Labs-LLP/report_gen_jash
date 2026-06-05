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
    <div style={{
      background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 12,
      padding: "1rem", position: "relative", gridColumn: wide ? "1 / -1" : "auto",
      opacity: editMode ? 0.96 : 1,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.6rem" }}>
        <strong style={{ fontSize: "0.9rem" }}>{title}</strong>
        {chips.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem" }}>
            {chips.map((c, i) => (
              <span key={i} style={{ fontSize: "0.62rem", background: t.bgPanel, border: `1px solid ${t.border}`, color: t.textMuted, borderRadius: 8, padding: "0.05rem 0.35rem" }}>{c}</span>
            ))}
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "0.25rem", marginLeft: "auto" }}>
          {spec.explanation && (
            <button title="Explain" onClick={() => onExplain({ title, explanation: spec.explanation, sql: spec.sql })} style={iconBtn(t)}>👁</button>
          )}
          <button title="AI Modify this chart" onClick={() => { setAiOpen((o) => !o); setDrawerOpen(false); }} style={iconBtn(t, aiOpen)}>✎</button>
          <div ref={dlRef} style={{ position: "relative" }}>
            <button title="Download this chart" onClick={(e) => { e.stopPropagation(); setDlOpen((o) => !o); }} style={iconBtn(t)}>⤓</button>
            {dlOpen && (
              <div style={{ position: "absolute", top: 30, right: 0, zIndex: 20, background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 8, boxShadow: t.shadow, overflow: "hidden", minWidth: 130 }}>
                <button onClick={() => { setDlOpen(false); downloadChartPNG(instRef.current, title); }} style={dlOpt(t)}>Download PNG</button>
                <button onClick={() => { setDlOpen(false); downloadChartPDF(instRef.current, title); }} style={dlOpt(t)}>Download PDF</button>
              </div>
            )}
          </div>
          <button title="Filter this chart" onClick={() => { setDrawerOpen((o) => !o); setAiOpen(false); }} style={iconBtn(t, drawerOpen || chips.length > 0)}>
            ⨉⃝
            {chips.length > 0 && (
              <span style={{ position: "absolute", top: -6, right: -6, background: t.accent, color: "#fff", borderRadius: "50%", fontSize: "0.6rem", minWidth: 15, height: 15, lineHeight: "15px", textAlign: "center", padding: "0 2px" }}>{chips.length}</span>
            )}
          </button>
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

function iconBtn(t, active) {
  return {
    position: "relative",
    border: `1px solid ${active ? t.accent : t.border}`,
    background: active ? t.accent : t.bgPanel,
    color: active ? "#fff" : t.textMuted,
    borderRadius: 6, width: 26, height: 26, cursor: "pointer",
    fontSize: "0.72rem", lineHeight: 1, display: "inline-flex", alignItems: "center", justifyContent: "center",
  };
}
function dlOpt(t) {
  return { display: "block", width: "100%", textAlign: "left", border: "none", background: "transparent", color: t.text, padding: "0.5rem 0.8rem", cursor: "pointer", fontSize: "0.76rem" };
}
