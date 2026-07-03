import { useEffect, useRef } from "react";
import Chart from "chart.js/auto";
import { getColors, formatNum, isPieType, isCurrencyColumn, formatColumnName } from "./format.js";

const ALPHA = "33";

// Renders one chart spec via Chart.js, managing the canvas lifecycle.
// - `spec` is the chart spec (type, color_scheme, x_label, y_label, ...).
// - `data` (optional) overrides spec.data — used by the per-chart filter drawer.
// - `onReady(instance, idx)` exposes the live Chart.js instance to the parent
//   (used for PDF image export + per-chart downloads).
export default function ChartView({ spec, data: dataOverride, theme, onReady, chartIdx, wide }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  const rows = dataOverride || (spec && spec.data) || [];

  useEffect(() => {
    if (!canvasRef.current || !spec || !Array.isArray(rows) || rows.length === 0) return;

    const instance = buildChart(canvasRef.current, spec, rows, theme);
    chartRef.current = instance;
    if (instance && onReadyRef.current) onReadyRef.current(instance, chartIdx);

    return () => {
      if (chartRef.current) {
        try { chartRef.current.destroy(); } catch { /* */ }
        chartRef.current = null;
      }
      if (onReadyRef.current) onReadyRef.current(null, chartIdx);
    };
    // Re-render when the data reference, type, scheme or theme changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, theme, spec.type, spec.color_scheme, spec.x_label, spec.y_label]);

  if (!spec || !Array.isArray(rows) || rows.length === 0) {
    return <div style={{ color: "#94a3b8", fontSize: "0.85rem", padding: "1rem" }}>No data for this chart.</div>;
  }
  return (
    <div style={{ position: "relative", height: wide ? 310 : 270 }}>
      <canvas ref={canvasRef} />
    </div>
  );
}

// Faithful port of renderChart() from report.js.
function buildChart(canvas, chartSpec, data, theme) {
  const keys = Object.keys(data[0]);
  if (keys.length === 0) return null;

  const labelKey = keys[0];
  const valueKeys = keys.length > 1 ? keys.slice(1) : [keys[0]];
  const labels = data.map((row) => String(row[labelKey]));

  const isDark = theme === "dark";
  const defaults = {
    gridColor: isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)",
    textColor: isDark ? "#94a3b8" : "#475569",
    bgColor: isDark ? "#161b22" : "#ffffff",
  };
  const colors = getColors(chartSpec.color_scheme, Math.max(data.length, valueKeys.length));

  let chartType = (chartSpec.type || "bar").toLowerCase();
  let isHorizontal = false, isStacked = false, isArea = false;

  if (chartType === "horizontalbar") { chartType = "bar"; isHorizontal = true; }
  else if (chartType === "stackedbar") { chartType = "bar"; isStacked = true; }
  else if (chartType === "area") { chartType = "line"; isArea = true; }
  else if (chartType === "polararea") { chartType = "doughnut"; }
  else if (chartType === "radar") { chartType = "bar"; }
  else if (!["bar", "line", "pie", "doughnut", "scatter"].includes(chartType)) { chartType = "bar"; }

  const datasets = valueKeys.map((key, i) => {
    const values = data.map((row) => {
      const v = row[key];
      return v === null || v === undefined ? 0 : Number(v) || 0;
    });
    const color = colors[i % colors.length];
    const cfg = {
      label: formatColumnName(key),
      data: values,
      backgroundColor: color + ALPHA,
      borderColor: color,
      borderWidth: 2,
    };

    if (chartType === "line") {
      cfg.tension = 0.45;
      const ptRadius = data.length > 50 ? 0 : data.length > 20 ? 2 : 4;
      cfg.pointRadius = ptRadius;
      cfg.pointHoverRadius = data.length > 50 ? 5 : 7;
      cfg.pointBackgroundColor = color;
      cfg.pointBorderColor = "#fff";
      cfg.pointBorderWidth = ptRadius > 0 ? 2 : 0;
      cfg.borderWidth = data.length > 50 ? 2 : 2.5;
      if (isArea) {
        cfg.fill = "origin";
        cfg.backgroundColor = (ctx) => {
          if (!ctx.chart.chartArea) return color + "18";
          const { top, bottom } = ctx.chart.chartArea;
          const g = ctx.chart.ctx.createLinearGradient(0, top, 0, bottom);
          g.addColorStop(0, color + "55");
          g.addColorStop(1, color + "04");
          return g;
        };
      } else {
        cfg.backgroundColor = color + "18";
      }
    }

    if (chartType === "bar") {
      cfg.borderRadius = isHorizontal ? 4 : 6;
      cfg.borderSkipped = false;
      cfg.borderWidth = 2;
      cfg.borderColor = color;
      cfg.backgroundColor = color + (isStacked ? "44" : "22");
      cfg.hoverBackgroundColor = color + "55";
      cfg.hoverBorderColor = color;
      cfg.hoverBorderWidth = 2.5;
    }

    if (isPieType(chartType)) {
      cfg.backgroundColor = colors.slice(0, values.length).map((c) => c + "bb");
      cfg.borderColor = defaults.bgColor;
      cfg.borderWidth = 2;
      cfg.hoverBackgroundColor = colors.slice(0, values.length).map((c) => c + "ee");
      cfg.hoverOffset = 10;
      cfg.hoverBorderWidth = 0;
    }

    if (chartType === "scatter") {
      // Two+ numeric value columns (e.g. unit_count, value) → the FIRST is the
      // x-axis and the SECOND is y, with the categorical label kept per-point
      // for tooltips. This only builds one dataset (the i===0 pass); skip the
      // second value column's own would-be dataset since it's consumed as y.
      // One numeric value column → fall back to using the label itself as x
      // (only sensible when the label is genuinely numeric, e.g. a day count).
      if (valueKeys.length >= 2) {
        if (i > 0) return null; // only emit once, built from valueKeys[0] and [1]
        const xKey = valueKeys[0], yKey = valueKeys[1];
        cfg.label = `${formatColumnName(xKey)} vs ${formatColumnName(yKey)}`;
        cfg.data = data.map((row) => ({
          x: Number(row[xKey]) || 0,
          y: Number(row[yKey]) || 0,
          _pointLabel: String(row[labelKey]),
        }));
      } else {
        cfg.data = data.map((row) => ({ x: Number(row[labelKey]) || 0, y: Number(row[key]) || 0 }));
      }
      cfg.pointRadius = 5;
      cfg.pointHoverRadius = 8;
      cfg.pointBackgroundColor = color + "cc";
      cfg.pointBorderColor = color;
      cfg.pointBorderWidth = 2;
    }
    return cfg;
  }).filter(Boolean);

  const canvasBgPlugin = {
    id: "canvasBackground",
    beforeDraw(chart) {
      const ctx = chart.ctx;
      ctx.save();
      ctx.globalCompositeOperation = "destination-over";
      ctx.fillStyle = defaults.bgColor === "#ffffff" ? "rgba(255,255,255,0.94)" : "rgba(22,27,34,0.94)";
      ctx.fillRect(0, 0, chart.width, chart.height);
      ctx.restore();
    },
  };

  const config = {
    type: chartType,
    data: { labels: chartType === "scatter" ? undefined : labels, datasets },
    plugins: [canvasBgPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      devicePixelRatio: Math.ceil(window.devicePixelRatio || 2),
      indexAxis: isHorizontal ? "y" : "x",
      interaction: {
        intersect: !(chartType === "line"),
        mode: chartType === "line" ? "index" : "nearest",
      },
      plugins: {
        legend: {
          display: valueKeys.length > 1 || isPieType(chartType),
          position: isPieType(chartType) ? "right" : "top",
          labels: {
            color: defaults.textColor,
            font: { family: "'Figtree', sans-serif", size: 11, weight: 500 },
            padding: 12, usePointStyle: true, pointStyleWidth: 8, boxWidth: 8, boxHeight: 8,
          },
        },
        tooltip: {
          enabled: true,
          backgroundColor: defaults.bgColor === "#ffffff" ? "rgba(15,23,42,0.96)" : "rgba(255,255,255,0.97)",
          titleColor: defaults.bgColor === "#ffffff" ? "#f1f5f9" : "#0f172a",
          bodyColor: defaults.bgColor === "#ffffff" ? "#e2e8f0" : "#334155",
          borderColor: defaults.bgColor === "#ffffff" ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
          borderWidth: 1,
          padding: { top: 10, bottom: 10, left: 14, right: 14 },
          cornerRadius: 10, caretSize: 6, caretPadding: 8,
          titleFont: { family: "'Figtree', sans-serif", size: 12, weight: 700 },
          bodyFont: { family: "'Figtree', sans-serif", size: 11, weight: 500 },
          titleMarginBottom: 8, bodySpacing: 6, boxPadding: 6,
          displayColors: true, usePointStyle: true,
          callbacks: {
            title: (items) => {
              if (!items.length) return "";
              if (chartType === "scatter" && valueKeys.length >= 2) {
                const raw = items[0].raw;
                if (raw && raw._pointLabel !== undefined) return raw._pointLabel;
              }
              return items[0].label || "";
            },
            label: (ctx) => {
              if (chartType === "scatter" && valueKeys.length >= 2) {
                const xKey = valueKeys[0], yKey = valueKeys[1];
                const xVal = ctx.parsed.x, yVal = ctx.parsed.y;
                return [
                  ` ${formatColumnName(xKey)}: ${formatNum(xVal, isCurrencyColumn(xKey))}`,
                  ` ${formatColumnName(yKey)}: ${formatNum(yVal, isCurrencyColumn(yKey))}`,
                ];
              }
              let val;
              if (isPieType(chartType)) val = ctx.parsed;
              else if (isHorizontal) val = ctx.parsed.x;
              else val = ctx.parsed.y;
              const dsLabel = ctx.dataset.label || valueKeys[ctx.datasetIndex] || "Value";
              const colName = valueKeys[ctx.datasetIndex] || dsLabel;
              const isCurr = isCurrencyColumn(colName);
              return ` ${dsLabel}: ${typeof val === "number" ? formatNum(val, isCurr) : String(val)}`;
            },
            afterLabel: (ctx) => {
              if (isPieType(chartType)) {
                const total = ctx.dataset.data.reduce((s, v) => s + (Number(v) || 0), 0);
                const val = Number(ctx.parsed) || 0;
                if (total > 0) return `  Share: ${((val / total) * 100).toFixed(1)}%`;
              }
              return "";
            },
          },
        },
      },
      scales: {},
      animation: { duration: 800, easing: "easeOutQuart" },
      onHover: (event, elements) => {
        if (event.native) event.native.target.style.cursor = elements.length ? "pointer" : "default";
      },
    },
  };

  if (!isPieType(chartType)) {
    const allNonCurrency = valueKeys.every((k) => !isCurrencyColumn(k));
    const numFmtCallback = (val) => (typeof val !== "number" ? val : formatNum(val, !allNonCurrency));
    const labelFmtCallback = function (val) {
      const label = this.getLabelForValue(val);
      if (typeof label === "string" && label.length > 18) return label.substring(0, 16) + "…";
      return label;
    };
    const valueAxisKey = isHorizontal ? "x" : "y";
    const labelAxisKey = isHorizontal ? "y" : "x";
    const avgLabelLen = labels.reduce((s, l) => s + l.length, 0) / Math.max(labels.length, 1);
    const smartRotation = isHorizontal ? 0 : (avgLabelLen > 12 ? 45 : (labels.length > 8 ? 35 : 0));
    config.options.scales = {
      [valueAxisKey]: {
        grid: { color: defaults.gridColor, drawBorder: false },
        ticks: { color: defaults.textColor, font: { family: "'Figtree'", size: 11, weight: 500 }, callback: numFmtCallback, maxTicksLimit: 8, padding: 4 },
        title: (isHorizontal ? chartSpec.x_label : chartSpec.y_label) ? {
          display: true, text: isHorizontal ? chartSpec.x_label : chartSpec.y_label,
          color: defaults.textColor, font: { family: "'Figtree'", size: 11, weight: 700 }, padding: { top: 8 },
        } : undefined,
        stacked: isStacked, beginAtZero: true,
      },
      [labelAxisKey]: {
        grid: { color: isHorizontal ? "transparent" : defaults.gridColor, drawBorder: false },
        ticks: {
          color: defaults.textColor, font: { family: "'Figtree'", size: isHorizontal ? 11 : 10, weight: 500 },
          maxRotation: smartRotation, minRotation: smartRotation > 0 ? smartRotation - 10 : 0,
          autoSkip: true, autoSkipPadding: 8, maxTicksLimit: isHorizontal ? 20 : 14,
          callback: isHorizontal ? undefined : labelFmtCallback,
        },
        title: (isHorizontal ? chartSpec.y_label : chartSpec.x_label) ? {
          display: true, text: isHorizontal ? chartSpec.y_label : chartSpec.x_label,
          color: defaults.textColor, font: { family: "'Figtree'", size: 11, weight: 700 }, padding: { top: 8 },
        } : undefined,
        stacked: isStacked,
      },
    };
  }

  return new Chart(canvas, config);
}
