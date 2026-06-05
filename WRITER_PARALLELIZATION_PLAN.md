# MOVE 2 (revised) — Parallelize the Report Writer Internally

> Goal: cut the Report Writer's ~59-90s WITHOUT changing the output. "Smarter, not less."
> Decided with Joel: no output-degrading shrink. This splits the writer's independent outputs
> across concurrent calls so the SAME total text is produced in less wall-clock time.

---

## Why the Writer is slow (root cause)
It generates ~12k OUTPUT tokens in ONE sequential call. LLMs produce output tokens one-at-a-time,
so wall-clock ≈ proportional to output length. The ONLY way to go faster without writing less is to
generate the independent parts CONCURRENTLY (each call produces fewer tokens, in parallel).

## What the Writer produces (STANDARD mode) + dependency analysis
| Output | Reads from | Depends on other outputs? | Independent? |
|---|---|---|---|
| 1. Executive summary | the report data (KPIs/charts values) | No — synthesizes data directly | ✅ |
| 2. KPI explanations (×6) | each KPI's data | No | ✅ |
| 3. Chart explanations (×6) | each chart's data | No | ✅ |
| 4. Insights (×6-8) | report data, cross-dimension | "go beyond KPI cards" but works from DATA, not the explanation prose | ✅ (see risk) |

All 4 read the SAME input (the data report) and write to DIFFERENT fields. No output feeds another.
→ Safe to generate in parallel, then assemble.

## Proposed split (2 concurrent calls — balanced, not 4)
Splitting into 4 tiny calls adds overhead (4× request latency + 4× cache reads). Better: 2 balanced groups.
- **Call A — "Explanations"**: KPI explanations + chart explanations (the bulk: 12 × 4-part = ~half the tokens).
- **Call B — "Narrative"**: executive summary + insights (the synthesis half).

Run A ∥ B on threads (same pattern as DA∥Writer). Then merge:
- final.kpis[i].explanation  ← from A
- final.charts[i].explanation ← from A
- final.summary              ← from B
- final.insights             ← from B
- data/sql fields            ← unchanged (preserved from input)

Wall-clock = max(A, B) ≈ ~half the current ~59-90s → est. **~30-45s saved**, output byte-equivalent in coverage.

## Honest risks + mitigations
1. **Insights quality could dip slightly** — currently the writer sees everything in one context, so insights
   can reference its own explanations. Split, Call B (insights) won't see Call A's explanation prose — BUT it
   still sees all the raw KPI/chart DATA, which is what insights are supposed to cite anyway (the prompt already
   says "cite actual data values", not "reference the explanations"). Low risk. Mitigation: give Call B the full
   data report (it already needs it).
2. **Two calls = slightly more input tokens** (each call re-sends the data report). Offset by cache (shared
   context already cached) + the data report is small (~few k tokens). Net still a win.
3. **Concurrency complexity** — same as DA∥Writer (already solved: thread-safe usage log exists).
4. **The insights fallback** (`_generate_insights_fallback`) and the QA-feedback retry path must still work →
   keep them; they operate on the merged result.
5. **DRIFT mode** has 9 narrative components (checklist, decision options, drivers...) — MORE complex. KEEP DRIFT
   SERIAL (single call) for now, parallelize STANDARD only (matches our DA∥Writer split; drift is rare).

## What stays EXACTLY the same (the promise)
- Same summary (same length/depth), same 6 KPI explanations, same 6 chart explanations, same 6-8 insights.
- Same JSON shape the frontend consumes. Same eye-modal content. Same insight cards.
- Only difference: produced via 2 concurrent calls instead of 1 → faster wall-clock.

## Test plan
Re-run inventory + category queries. Expect: total time down another ~30-45s (toward ~110-130s), report
content equivalent (summary present, all explanations present, 6-8 insights, QA score holds). Watch insights
quality specifically (the one risk). Log before/after in OPTIMIZATION_PLAYBOOK.md.

## Verdict
This is the legitimate "same output, faster" win you asked for — it parallelizes WORK, doesn't cut CONTENT.
