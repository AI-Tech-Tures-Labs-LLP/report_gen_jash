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

// html2canvas's default rasterizer washes saturated colors out toward white
// (a long-standing, never-root-caused class of html2canvas issue — verified
// here directly: a `#6366f1` (99,102,241) reference swatch captured alone
// came back around (241,245,249), i.e. pulled almost all the way to white).
// Near-white pixels are essentially unaffected (there's nowhere left to wash
// out to), so the fix has to leave white alone and pull saturated colors back
// down — a plain multiply on every pixel does the opposite (it also drags
// true white toward the accent hue). This measures the real gap on an
// isolated reference element, then applies a contrast stretch anchored at
// white (255 always maps to 255) so backgrounds are untouched while washed
// colors get pulled back toward their real value.
async function measureWashoutGamma(html2canvas) {
  const ref = document.createElement("div");
  ref.style.cssText = "position:fixed;left:-9999px;top:0;width:40px;height:40px;background:#6366f1;";
  document.body.appendChild(ref);
  try {
    const refCanvas = await html2canvas(ref, { scale: 2, logging: false, backgroundColor: "#ffffff" });
    const ctx = refCanvas.getContext("2d");
    const [r, g, b] = ctx.getImageData(refCanvas.width >> 1, refCanvas.height >> 1, 1, 1).data;
    const target = [99, 102, 241];
    const sample = [r, g, b];
    // Contrast-from-white factor per channel: solves 255-(255-sample)*k = target.
    const gammas = sample.map((s, i) => {
      const denom = 255 - s;
      if (denom < 4) return 1; // sample already ~white, can't derive a factor — skip correction
      const k = (255 - target[i]) / denom;
      return Math.min(Math.max(k, 1), 6); // only ever strengthen color, and bound it sanely
    });
    console.log("[pdf-washout] sample", sample, "gammas", gammas);
    return gammas;
  } finally {
    document.body.removeChild(ref);
  }
}

function applyWashoutCorrection(canvas, gammas) {
  const ctx = canvas.getContext("2d");
  const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    d[i] = Math.min(255, Math.max(0, 255 - (255 - d[i]) * gammas[0]));
    d[i + 1] = Math.min(255, Math.max(0, 255 - (255 - d[i + 1]) * gammas[1]));
    d[i + 2] = Math.min(255, Math.max(0, 255 - (255 - d[i + 2]) * gammas[2]));
  }
  ctx.putImageData(img, 0, 0);
}

// Full-report PDF built by screenshotting the live, already-styled report DOM
// (via html2canvas) and laying it into A4 pages (via jsPDF).
//
// The whole container is captured as ONE canvas rather than one canvas per
// section: capturing sections independently loses their real ancestor
// backdrop, so semi-transparent card/accent `rgba()` colors — which are
// designed to blend against the page background — composite against nothing
// and render washed out. Capturing the full container preserves the exact
// same DOM ancestry and compositing the browser already used to paint it.
//
// Page breaks are then chosen from the single tall canvas, but only at gaps
// *between* `.rpt-section` elements (using each section's measured top/bottom
// offset within the container) — never mid-section — so a KPI grid, a chart,
// the table, or an insight row is never cut across a page boundary.
export async function exportPDF(report, containerEl) {
  const html2canvas = (await import("html2canvas")).default;
  const pdf = new jsPDF("p", "mm", "a4");
  const W = 210, H = 297, M = 10;
  const usableW = W - M * 2;
  const usableH = H - M * 2;

  if (!containerEl) {
    pdf.setFont("helvetica", "bold");
    pdf.setFontSize(14);
    pdf.text(safeText(report.title || "Analytics Report"), M, M + 6);
    pdf.save((report.title || "Analytics_Report").replace(/[^a-zA-Z0-9_\- ]/g, "").replace(/\s+/g, "_") + ".pdf");
    return;
  }

  const containerRect = containerEl.getBoundingClientRect();
  const sectionEls = Array.from(containerEl.querySelectorAll(".rpt-section"));
  // Section boundaries in CSS px, relative to the top of the captured container.
  const sectionBounds = sectionEls.map((el) => {
    const r = el.getBoundingClientRect();
    return { top: r.top - containerRect.top, bottom: r.bottom - containerRect.top };
  });

  const scale = 2;
  // No `backgroundColor` override here on purpose: the container already has
  // its real page background painted inline (t.bg), and every rgba() card/
  // accent color inside it was authored to blend against that exact
  // background. Forcing a different background (e.g. white) at the html2canvas
  // level composites those rgba() layers against the wrong backdrop and
  // washes them out.
  const [canvas, gammas] = await Promise.all([
    html2canvas(containerEl, { scale, useCORS: true, logging: false }),
    measureWashoutGamma(html2canvas),
  ]);
  applyWashoutCorrection(canvas, gammas);

  const pxToMm = usableW / canvas.width; // canvas px -> PDF mm, at full container width
  const pageCanvasH = usableH / pxToMm; // how many canvas px fit on one PDF page

  // Snap a candidate cut line (in canvas px) up to the nearest section boundary
  // that is <= it, so a small section (KPI grid, summary, insights) is never
  // split. Only accepted if it doesn't waste more than ~15% of a page — a
  // section too tall to share a page with anything (e.g. the full charts
  // grid) would otherwise leave most of a page blank; in that case we fall
  // back to a raw page-height cut, which lands inside a grid of independent
  // cards rather than through a single card (grids reflow safely, so a
  // mid-grid cut is visually harmless, unlike cutting through one card).
  const maxWastePx = pageCanvasH * 0.15;
  function snapToSectionGap(candidatePx) {
    let best = null;
    for (const b of sectionBounds) {
      const bottomPx = b.bottom * scale;
      const topPx = b.top * scale;
      if (bottomPx <= candidatePx && (best === null || bottomPx > best)) best = bottomPx;
      // If the candidate would land inside this section, cut before it instead.
      if (topPx < candidatePx && candidatePx < bottomPx) {
        if (best === null || topPx > best) best = topPx;
      }
    }
    if (best === null || best <= 0) return candidatePx;
    if (candidatePx - best > maxWastePx) return candidatePx; // too much wasted space — use the raw cut instead
    return best;
  }

  let offset = 0;
  let first = true;
  while (offset < canvas.height) {
    const remaining = canvas.height - offset;
    let sliceH = Math.min(pageCanvasH, remaining);
    if (sliceH < remaining) sliceH = snapToSectionGap(offset + sliceH) - offset;
    if (sliceH <= 0) sliceH = Math.min(pageCanvasH, remaining); // safety net: no valid gap found

    const slice = document.createElement("canvas");
    slice.width = canvas.width;
    slice.height = sliceH;
    slice.getContext("2d").drawImage(canvas, 0, offset, canvas.width, sliceH, 0, 0, canvas.width, sliceH);

    if (!first) pdf.addPage();
    first = false;
    pdf.addImage(slice.toDataURL("image/png", 1.0), "PNG", M, M, usableW, sliceH * pxToMm);
    offset += sliceH;
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
