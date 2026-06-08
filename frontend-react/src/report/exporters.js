// PDF + Excel export for the report. Ported from report.js.
import { jsPDF } from "jspdf";
import * as XLSX from "xlsx";

// Normalize text for PDF standard fonts (Helvetica) to prevent spacing/shattering bugs
// caused by unsupported Unicode characters like the Indian Rupee symbol (₹) or special bullet triangles.
function safeText(str) {
  if (str == null) return "";
  return String(str)
    .replace(/₹/g, "Rs. ")
    .replace(/[▶►●•]/g, "- ")
    .replace(/[^\x00-\x7F]/g, " "); // Replace other high-unicode chars with spaces to prevent width corruption
}

// Full-report PDF with embedded chart images. Faithful port of the vanilla
// downloadPdfBtn handler (title, accent line, date, summary box, KPI grid,
// chart images, insights). `chartInstances` is a map { idx: Chart.js instance }.
export function exportPDF(report, chartInstances = {}) {
  const pdf = new jsPDF("p", "mm", "a4");
  const W = 210, H = 297, M = 14;
  const usableW = W - M * 2;
  let y = M;

  function ensureSpace(needed) {
    if (y + needed > H - M) { pdf.addPage(); y = M; }
  }
  function wrapText(text, maxWidth, fontSize) {
    pdf.setFontSize(fontSize);
    return pdf.splitTextToSize(safeText(text), maxWidth);
  }

  // ── Title ──
  pdf.setFont("helvetica", "bold");
  pdf.setFontSize(18);
  pdf.setTextColor(30, 30, 60);
  pdf.text(safeText(report.title || "Analytics Report"), M, y + 6);
  y += 12;

  // Accent line
  pdf.setDrawColor(99, 102, 241);
  pdf.setLineWidth(0.8);
  pdf.line(M, y, M + 60, y);
  y += 6;

  // ── Date ──
  pdf.setFont("helvetica", "normal");
  pdf.setFontSize(8);
  pdf.setTextColor(120, 120, 140);
  pdf.text("Generated: " + new Date().toLocaleString(), M, y);
  y += 8;

  // ── Summary ──
  if (report.summary) {
    ensureSpace(30);
    pdf.setFillColor(240, 244, 255);
    const summaryLines = wrapText(report.summary, usableW - 8, 9);
    const summaryH = summaryLines.length * 4.5 + 8;
    pdf.roundedRect(M, y, usableW, summaryH, 3, 3, "F");
    pdf.setFillColor(99, 102, 241);
    pdf.rect(M, y, 1.5, summaryH, "F");
    pdf.setFont("helvetica", "normal");
    pdf.setFontSize(9);
    pdf.setTextColor(50, 50, 70);
    pdf.text(summaryLines, M + 6, y + 6);
    y += summaryH + 6;
  }

  // ── KPIs ──
  const kpis = (report.kpis || []).filter((k) => !k._placeholder);
  if (kpis.length > 0) {
    ensureSpace(30);
    pdf.setFont("helvetica", "bold");
    pdf.setFontSize(10);
    pdf.setTextColor(16, 185, 129);
    pdf.text("KEY PERFORMANCE INDICATORS", M, y + 4);
    y += 8;

    const cols = 3;
    const cardW = (usableW - (cols - 1) * 4) / cols;
    const cardH = 20;
    const accentColors = [
      [16, 185, 129], [59, 130, 246], [139, 92, 246],
      [245, 158, 11], [244, 63, 94], [6, 182, 212],
    ];
    for (let i = 0; i < kpis.length; i++) {
      const col = i % cols;
      if (col === 0 && i > 0) { y += cardH + 4; ensureSpace(cardH + 4); }
      const x = M + col * (cardW + 4);
      const cy = y;
      pdf.setFillColor(248, 249, 250);
      pdf.roundedRect(x, cy, cardW, cardH, 2, 2, "F");
      const ac = accentColors[i % accentColors.length];
      pdf.setFillColor(ac[0], ac[1], ac[2]);
      pdf.rect(x, cy, cardW, 1.5, "F");
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(6.5);
      pdf.setTextColor(100, 100, 120);
      pdf.text(safeText(kpis[i].label).substring(0, 30).toUpperCase(), x + 3, cy + 6);
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(13);
      pdf.setTextColor(30, 30, 50);
      pdf.text(safeText(kpis[i].value ?? "—").substring(0, 20), x + 3, cy + 14);
    }
    y += cardH + 8;
  }

  // ── Charts (as images) ──
  const charts = report.charts || [];
  const haveChartImages = Object.keys(chartInstances).length > 0;
  if (charts.length > 0 && haveChartImages) {
    ensureSpace(20);
    pdf.setFont("helvetica", "bold");
    pdf.setFontSize(10);
    pdf.setTextColor(99, 102, 241);
    pdf.text("CHARTS & VISUALIZATIONS", M, y + 4);
    y += 10;

    for (let i = 0; i < charts.length; i++) {
      const inst = chartInstances[i];
      if (!inst) continue;
      let chartImg;
      try { chartImg = inst.toBase64Image("image/png", 1.0); } catch { continue; }
      const chartTitle = safeText(charts[i].title || "Chart " + (i + 1));
      const canvas = inst.canvas;
      const aspect = canvas.width / canvas.height || 1.6;
      let imgW = usableW;
      let imgH = imgW / aspect;
      if (imgH > 100) { imgH = 100; imgW = imgH * aspect; }
      const cardH = imgH + 14;
      ensureSpace(cardH + 4);
      pdf.setFillColor(255, 255, 255);
      pdf.setDrawColor(220, 220, 230);
      pdf.roundedRect(M, y, usableW, cardH, 2, 2, "FD");
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(9);
      pdf.setTextColor(30, 30, 60);
      pdf.text(chartTitle, M + 4, y + 7);
      const imgX = M + (usableW - imgW) / 2;
      pdf.addImage(chartImg, "PNG", imgX, y + 11, imgW, imgH);
      y += cardH + 5;
    }
  }

  // ── Insights ──
  const insights = report.insights || [];
  if (insights.length > 0) {
    ensureSpace(20);
    pdf.setFont("helvetica", "bold");
    pdf.setFontSize(10);
    pdf.setTextColor(245, 158, 11);
    pdf.text("KEY INSIGHTS", M, y + 4);
    y += 8;
    const insightColors = {
      positive: [16, 185, 129], negative: [244, 63, 94], warning: [244, 63, 94],
      neutral: [59, 130, 246], opportunity: [245, 158, 11],
    };
    insights.forEach((ins) => {
      const title = safeText(typeof ins === "string" ? ins : (ins.title || ""));
      const body = safeText(typeof ins === "string" ? "" : (ins.body || ins.text || ""));
      const type = (typeof ins === "object" && ins.type ? ins.type : "neutral").toLowerCase();
      const color = insightColors[type] || insightColors.neutral;
      const bodyLines = wrapText(body, usableW - 12, 8);
      const cardH = 8 + bodyLines.length * 3.8 + 4;
      ensureSpace(cardH + 3);
      pdf.setFillColor(252, 252, 253);
      pdf.roundedRect(M, y, usableW, cardH, 2, 2, "F");
      pdf.setFillColor(color[0], color[1], color[2]);
      pdf.rect(M, y, 1.5, cardH, "F");
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(8.5);
      pdf.setTextColor(30, 30, 60);
      pdf.text(title, M + 5, y + 5.5);
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(8);
      pdf.setTextColor(70, 70, 90);
      pdf.text(bodyLines, M + 5, y + 10);
      y += cardH + 3;
    });
  }

  pdf.save((report.title || "Analytics_Report").replace(/[^a-zA-Z0-9_\- ]/g, "").replace(/\s+/g, "_") + ".pdf");
}

// Excel: KPI sheet + one sheet per chart + the detail table.
export function exportExcel(report) {
  const wb = XLSX.utils.book_new();
  const kpis = (report.kpis || []).filter((k) => !k._placeholder);
  if (kpis.length) {
    const rows = kpis.map((k) => ({ KPI: k.label || k.title || "—", Value: k.value != null ? k.value : "N/A", SQL: k.sql || "" }));
    const ws = XLSX.utils.json_to_sheet(rows);
    ws["!cols"] = [{ wch: 30 }, { wch: 20 }, { wch: 60 }];
    XLSX.utils.book_append_sheet(wb, ws, "KPIs");
  }
  (report.charts || []).forEach((c, i) => {
    if (Array.isArray(c.data) && c.data.length) {
      const name = (c.title || `Chart ${i + 1}`).replace(/[\\/?*[\]:]/g, "").substring(0, 31) || `Chart ${i + 1}`;
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(c.data), name);
    }
  });
  if (report.table && Array.isArray(report.table.data) && report.table.data.length) {
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(report.table.data), "Detail Table");
  }
  XLSX.writeFile(wb, (report.title || "Analytics_Report").replace(/[^a-zA-Z0-9_\- ]/g, "").replace(/\s+/g, "_") + ".xlsx");
}

// Per-chart PNG download. Faithful port of _wireChartDownloads (png branch).
export function downloadChartPNG(instance, title) {
  if (!instance) return;
  const safe = (title || "Chart").replace(/[^a-zA-Z0-9_\- ]/g, "").replace(/\s+/g, "_");
  const imgData = instance.toBase64Image("image/png", 1.0);
  const link = document.createElement("a");
  link.download = `${safe}.png`;
  link.href = imgData;
  link.click();
}

// Per-chart single-page PDF (image centered). Faithful port of the pdf branch.
export function downloadChartPDF(instance, title) {
  if (!instance) return;
  const safe = (title || "Chart").replace(/[^a-zA-Z0-9_\- ]/g, "").replace(/\s+/g, "_");
  const imgData = instance.toBase64Image("image/png", 1.0);
  const img = new Image();
  img.onload = () => {
    const ratio = img.width / img.height;
    const pdfWidth = 210;
    const margin = 12;
    const usable = pdfWidth - margin * 2;
    const imgH = usable / ratio;
    const pdf = new jsPDF("l", "mm", [pdfWidth, Math.max(imgH + margin * 2 + 20, 150)]);
    pdf.setFontSize(14);
    pdf.text(title || "Chart", margin, margin + 5);
    pdf.addImage(imgData, "PNG", margin, margin + 12, usable, imgH);
    pdf.save(`${safe}.pdf`);
  };
  img.src = imgData;
}
