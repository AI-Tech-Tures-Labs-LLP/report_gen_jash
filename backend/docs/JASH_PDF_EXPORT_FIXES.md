# PDF Export Fixes (Jash)

## Background

The "Download PDF" button on the report page (`ReportPage.jsx`) previously built the PDF by hand-drawing every element with raw jsPDF primitives (rects, text, chart images pulled from Chart.js canvases) — a layout that was never updated to match the actual styled web UI, so the exported PDF looked like a generic wireframe: tiny KPI cards with truncated text, chart images squashed to a fixed box regardless of aspect ratio, and no visual parity with the live report.

Rewrote `exportPDF()` in `frontend-react/src/report/exporters.js` to screenshot the live, already-styled report DOM instead, so the PDF matches the web page exactly.

## Problem 1 — Hand-drawn PDF looked nothing like the web report

**Fix:** Switched to capturing the live DOM via `html2canvas` and placing the screenshot into A4 pages via `jsPDF`, instead of manually drawing shapes/text. Added a `reportBodyRef` in `ReportPage.jsx` pointing at the report body container, and a title/date block inside that container (previously the title only existed in the sticky topbar, which isn't part of the exported area).

## Problem 2 — Colors washed out per-section capture

Initial version captured each `.rpt-section` independently. This lost the section's real ancestor background, so semi-transparent `rgba()` card/accent colors (designed to blend against the page background `#f1f5f9`) composited against nothing and rendered pale.

**Fix:** Capture the *entire* report container as one screenshot instead of one per section — this preserves the exact same DOM ancestry and compositing the browser already used to paint it.

## Problem 3 — Sections cut in half across page breaks

**Fix:** Page breaks are chosen from the single tall canvas, snapped to the gaps *between* `.rpt-section` elements (measured via `getBoundingClientRect()`) so a KPI grid, a chart, the table, or an insight row is never split across two pages.

**Follow-up bug:** Snapping to a section boundary unconditionally could strand almost a full page of blank space when the next section didn't fit at all (e.g. a short KPI section stranded alone before a tall charts grid). Fixed by only snapping if the wasted space is under ~15% of a page; otherwise it falls back to a raw page-height cut, which lands inside a grid of independent chart/insight cards (safe to split) rather than through a single card.

## Problem 4 — html2canvas washes out saturated colors

Even with full-container capture, colors still rendered visibly duller/lighter than the live page — a long-standing, never-root-caused class of html2canvas issue (it reimplements CSS painting itself rather than using the browser's native compositor).

Tried and discarded:
- Forcing `backgroundColor: "#ffffff"` on the html2canvas call — didn't help, since the real issue wasn't the outer background.
- A blind global contrast/saturation/gamma pixel boost — didn't converge, and risked shifting hues.
- `foreignObjectRendering: true` (delegates painting to the browser's native SVG renderer) — this *did* produce perfect colors, but broke geometry: it mis-measures wide fixed-width containers, causing the exported content to shift left and get cut in half. Reverted in favor of the default rasterizer.
- A calibration pass that sampled a small reference swatch's color *within* the big captured canvas — failed because the coordinate math for locating the swatch was wrong, so it sampled the page background instead of the actual swatch, producing a nonsense correction that turned the entire PDF purple.

**Final fix:** `measureWashoutGamma()` renders a small, isolated `#6366f1` reference `<div>` off-screen and captures it *alone* through the exact same `html2canvas` call — no coordinate-hunting through the full page canvas, so the sample is always correct. It compares the captured color to the known true color and derives a per-channel correction factor using a **contrast stretch anchored at white** (`255 - (255 - pixel) * k`), not a flat multiply — this is the key detail: a flat multiply also drags true white/near-white backgrounds toward the accent hue (this was the cause of the "everything is purple" regression), whereas anchoring at white leaves backgrounds untouched and only pulls genuinely saturated colors back toward their real value. `applyWashoutCorrection()` then applies that correction to the whole captured canvas before it's sliced into PDF pages.

## Files changed

| File | Change |
|---|---|
| `frontend-react/src/report/exporters.js` | Rewrote `exportPDF()`: full-container `html2canvas` capture, section-aware page-break snapping with waste guard, `measureWashoutGamma()` + `applyWashoutCorrection()` for color fidelity. Added `html2canvas` as a dependency. |
| `frontend-react/src/report/ReportPage.jsx` | Added `reportBodyRef` on the report body container; added a title/date block inside the captured area (previously only in the topbar); wired the PDF button to the new async `exportPDF(report, containerEl)` signature with an "Exporting…" loading state. |
| `frontend-react/package.json` | Added `html2canvas` dependency. |

No backend changes were needed — this is purely a frontend export-rendering change and does not affect report generation, filtering, or data accuracy.
