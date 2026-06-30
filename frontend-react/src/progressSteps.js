// Curated, end-user-facing progression for the chat stream.
//
// The backend streams fine-grained SSE stages (routing → routed → analyze → sql →
// execute → interpret → complete) AND, internally, retries/validation blocks. We
// deliberately surface only a CLEAN, FORWARD-ONLY sequence of friendly steps — never
// raw SQL, errors, "BLOCKED", or retry noise — because exposing the model's stumbles
// to end users erodes trust. Backend stages collapse into these few labeled steps.

// Ordered list of the user-facing steps and which backend stages map to each.
// `key` is the step id; `label` is what the user reads. A stage not listed here is
// ignored (it just keeps the current step visible).
export const CHAT_STEPS = [
  { key: "understand", label: "Understanding your question", stages: ["routing", "routed", "analyze"] },
  { key: "query",      label: "Writing the query",           stages: ["sql"] },
  { key: "run",        label: "Running it against your data", stages: ["execute"] },
  { key: "answer",     label: "Preparing your answer",        stages: ["interpret"] },
];

// Minimum time (ms) a step stays visible before the next one can replace it, so a
// fast (~4s) query animates smoothly instead of strobing through every step at once.
export const MIN_STEP_MS = 450;

// Map a backend stage name → the index of the step it belongs to (or -1 if none).
export function stepIndexForStage(stage) {
  return CHAT_STEPS.findIndex((s) => s.stages.includes(stage));
}

// Build the "Running it — N rows found" suffix from the execute stage's payload.
// Returns "" when there is no usable count, so the base label is shown unchanged.
// Guards (see "honest caveat" in the design): a single-row result is almost always a
// scalar answer (one SUM/COUNT) — "1 row found" reads as confusing, so we suppress the
// count there. When the backend truncated the result set (truncated:true), we show "N+"
// so the count is never read as the true total.
export function rowCountSuffix(rowCount, truncated = false) {
  if (typeof rowCount !== "number" || rowCount < 0) return "";
  if (rowCount === 0) return " — no matching rows";
  if (rowCount === 1) return "";                       // scalar answer — don't show "1 row"
  const n = rowCount.toLocaleString();
  return ` — ${n}${truncated ? "+" : ""} rows found`;
}

// ── Derived "reasoning" line (Option 3) ──────────────────────────────────────
//
// A short, confident, PROCESS-ONLY narration shown under the active step. It is
// TEMPLATED from real routing flags (mode/complexity) and the literal row count —
// NO LLM, so it cannot hallucinate. Five guarantees enforced here:
//   1. Process language only — never states a data value except the literal row count.
//   2. Templated from real flags — fixed phrases, no generation.
//   3. Lags reality — only narrates what the system has ALREADY produced at this step
//      (e.g. it never names tables at the "query" step, because the SQL may still be
//      rewritten on a validation block).
//   4. Forward-only — on a retry the step stays put; we never narrate the stumble.
//   5. Row count guarded (via rowCountSuffix) — suppressed for 1-row, "+" on truncation.
//
// `ctx` = { mode, complexity, rowCount, truncated }. Returns "" when there's nothing
// safe/useful to add (the step label alone is shown).
export function reasoningFor(stepKey, ctx = {}) {
  const { mode, complexity, rowCount, truncated } = ctx;
  const isSimple = complexity === "simple";

  switch (stepKey) {
    case "understand":
      // Keyed on mode — a real decision the router already committed to.
      if (mode === "report") return "This looks like a broad request, so I'm preparing a full report.";
      if (isSimple) return "This looks like a focused, single lookup.";
      return "This needs a bit of analysis across your data.";

    case "query":
      // Deliberately vague: the final SQL isn't settled (could be rewritten), so we do
      // NOT name tables/columns here. Process statement only.
      return isSimple
        ? "Putting together a quick query."
        : "Working out the right query for this.";

    case "run":
      // Only fires meaningfully once execute has returned — so the count is real.
      if (typeof rowCount === "number") {
        if (rowCount === 0) return "The query ran, but nothing matched those filters.";
        if (rowCount === 1) return "Got the figure you asked for.";
        return `Pulled ${rowCount.toLocaleString()}${truncated ? "+" : ""} matching records.`;
      }
      return "Running it against your data.";

    case "answer":
      return "Summarizing what the results show.";

    default:
      return "";
  }
}
