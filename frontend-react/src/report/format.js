// Number/label formatting utilities, ported from the vanilla report.js.

// Indian short notation: 1.23 Cr / 12.3 L / 5.2K
export function shortNum(val) {
  const n = Number(val);
  if (!isFinite(n)) return String(val);
  const abs = Math.abs(n);
  if (abs >= 1e7) return (n / 1e7).toFixed(2).replace(/\.00$/, "") + " Cr";
  if (abs >= 1e5) return (n / 1e5).toFixed(2).replace(/\.00$/, "") + " L";
  if (abs >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return n.toLocaleString("en-IN");
}

export function isPieType(type) {
  const t = (type || "").toLowerCase();
  return t === "pie" || t === "doughnut";
}

export function isCurrencyColumn(name) {
  const n = (name || "").toLowerCase();
  if (/\b(count|qty|quantity|rate|pct|percent|number|num|days?)\b/.test(n)) return false;
  return /(revenue|amount|value|price|sales|cost|total|margin|spend|payment|balance|aov)/.test(n);
}

export function formatColumnName(name) {
  return String(name || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// Format a KPI value with optional format hint + Indian notation.
export function formatKPIValue(value, format) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!isFinite(n)) return String(value);
  if (format === "percent") return n.toFixed(2) + "%";
  const prefix = format === "currency" ? "₹" : "";
  const abs = Math.abs(n);
  if (abs >= 1e7) return prefix + (n / 1e7).toFixed(2) + " Cr";
  if (abs >= 1e5) return prefix + (n / 1e5).toFixed(2) + " L";
  return prefix + n.toLocaleString("en-IN");
}

// Format a numeric value for chart axes/tooltips, currency-aware.
export function formatNum(val, currency) {
  const n = Number(val);
  if (!isFinite(n)) return String(val);
  const p = currency ? "₹" : "";
  const abs = Math.abs(n);
  if (abs >= 1e7) return p + (n / 1e7).toFixed(2) + " Cr";
  if (abs >= 1e5) return p + (n / 1e5).toFixed(2) + " L";
  if (abs >= 1e3) return p + (n / 1e3).toFixed(1) + "K";
  return p + n.toLocaleString("en-IN");
}

// Color palettes (ported). Returns `count` colors, cycling if needed.
const PALETTES = {
  blues: ["#3b82f6", "#2563eb", "#1d4ed8", "#60a5fa", "#93c5fd", "#1e40af"],
  golds: ["#d4af37", "#b8860b", "#daa520", "#f0c75e", "#9c7a1a", "#e8c252"],
  purples: ["#8b5cf6", "#7c3aed", "#a78bfa", "#6d28d9", "#c4b5fd", "#5b21b6"],
  oranges: ["#f97316", "#ea580c", "#fb923c", "#c2410c", "#fdba74", "#9a3412"],
  mixed: ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6",
          "#ef4444", "#14b8a6", "#f97316", "#a855f7", "#06b6d4", "#84cc16",
          "#e11d48", "#0ea5e9", "#d4af37"],
  gradient: ["#6366f1", "#7c74f0", "#9583ef", "#ad8fee", "#c49ced", "#dba9ec"],
};

export function getColors(scheme, count) {
  const pal = PALETTES[(scheme || "mixed").toLowerCase()] || PALETTES.mixed;
  const out = [];
  for (let i = 0; i < count; i++) out.push(pal[i % pal.length]);
  return out;
}
