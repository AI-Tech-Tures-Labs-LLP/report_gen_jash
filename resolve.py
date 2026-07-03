import re

with open('frontend-react/src/App.jsx', 'r') as f:
    text = f.read()

# Fix Conflict 1
c1 = """<<<<<<< HEAD
  async function handleDirectReport(question) {
    if (loading) return;
    setLoading(true);
    setStatus("Generating report directly…");
    pushMessage({ role: "user", text: question });

    // register conversation in sidebar
    setConvs((prev) => {
      if (prev.find((c) => c.id === convId)) return prev;
      const next = [{ id: convId, title: question.slice(0, 40) }, ...prev];
      saveConversations(next);
      return next;
    });

    try {
      // Directly call /report — skips intent classification & schema warm for speed
      const reportData = await generateReport(question);
      const reportId = openReport(question, reportData, convId);
      pushMessage({ role: "ai", reportId });
    } catch (err) {
      pushMessage({ role: "ai", error: err.message || "Report generation failed." });
    } finally {
      setLoading(false);
      setStatus("");
    }
=======
  // Advance the VISIBLE step toward the target, never faster than MIN_STEP_MS between
  // advances, so a fast query animates step-by-step instead of jumping straight to the
  // last step. Steps only ever move forward (backend stages can arrive slightly out of
  // order on retries; we ignore any that would move us backward).
  function paceToStep(target) {
    if (target <= shownStepRef.current) return; // never go backward
    if (paceTimerRef.current) return;            // an advance is already scheduled
    const elapsed = Date.now() - lastAdvanceRef.current;
    const wait = Math.max(0, MIN_STEP_MS - elapsed);
    const doAdvance = () => {
      paceTimerRef.current = null;
      const next = shownStepRef.current + 1;
      if (next > stepRef.current) return; // nothing new to show
      shownStepRef.current = next;
      lastAdvanceRef.current = Date.now();
      setStepIndex(next);
      if (next < stepRef.current) paceToStep(stepRef.current); // keep catching up
    };
    if (wait === 0) doAdvance();
    else paceTimerRef.current = setTimeout(doAdvance, wait);
  }

  function resetProgress() {
    if (paceTimerRef.current) { clearTimeout(paceTimerRef.current); paceTimerRef.current = null; }
    stepRef.current = -1;
    shownStepRef.current = -1;
    lastAdvanceRef.current = 0;
    setStepIndex(-1);
    setRowCount(null);
    setTruncated(false);
    setRouteMode(null);
    setComplexity(null);
    setReportMode(false);
>>>>>>> origin/joelsrgv1exp1"""

c1_res = """  async function handleDirectReport(question) {
    if (loading) return;
    setLoading(true);
    resetProgress();
    setReportMode(true);
    setStepIndex(0);
    pushMessage({ role: "user", text: question });

    // register conversation in sidebar
    setConvs((prev) => {
      if (prev.find((c) => c.id === convId)) return prev;
      const next = [{ id: convId, title: question.slice(0, 40) }, ...prev];
      saveConversations(next);
      return next;
    });

    try {
      // Directly call /report — skips intent classification & schema warm for speed
      const reportData = await generateReport(question);
      const reportId = openReport(question, reportData, convId);
      pushMessage({ role: "ai", reportId });
    } catch (err) {
      pushMessage({ role: "ai", error: err.message || "Report generation failed." });
    } finally {
      setLoading(false);
      resetProgress();
    }
  }

  // Advance the VISIBLE step toward the target, never faster than MIN_STEP_MS between
  // advances, so a fast query animates step-by-step instead of jumping straight to the
  // last step. Steps only ever move forward (backend stages can arrive slightly out of
  // order on retries; we ignore any that would move us backward).
  function paceToStep(target) {
    if (target <= shownStepRef.current) return; // never go backward
    if (paceTimerRef.current) return;            // an advance is already scheduled
    const elapsed = Date.now() - lastAdvanceRef.current;
    const wait = Math.max(0, MIN_STEP_MS - elapsed);
    const doAdvance = () => {
      paceTimerRef.current = null;
      const next = shownStepRef.current + 1;
      if (next > stepRef.current) return; // nothing new to show
      shownStepRef.current = next;
      lastAdvanceRef.current = Date.now();
      setStepIndex(next);
      if (next < stepRef.current) paceToStep(stepRef.current); // keep catching up
    };
    if (wait === 0) doAdvance();
    else paceTimerRef.current = setTimeout(doAdvance, wait);
  }

  function resetProgress() {
    if (paceTimerRef.current) { clearTimeout(paceTimerRef.current); paceTimerRef.current = null; }
    stepRef.current = -1;
    shownStepRef.current = -1;
    lastAdvanceRef.current = 0;
    setStepIndex(-1);
    setRowCount(null);
    setTruncated(false);
    setRouteMode(null);
    setComplexity(null);
    setReportMode(false);
  }"""

text = text.replace(c1, c1_res)

# Fix Conflict 2
c2 = """<<<<<<< HEAD
          {loading && status && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", color: t.textMuted, fontSize: "0.82rem", padding: "0.5rem 0.25rem" }}>
              <span style={{ width: 14, height: 14, border: `2px solid ${t.border}`, borderTopColor: "#DB8310", borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
              <span style={{ fontWeight: 500 }}>{status}</span>
            </div>
=======
          {loading && stepIndex >= 0 && (
            reportMode ? (
              <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", color: t.textMuted, fontSize: "0.82rem", padding: "0.5rem 0.25rem" }}>
                <span style={{ width: 14, height: 14, border: `2px solid ${t.border}`, borderTopColor: "#d4af37", borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
                <span style={{ fontWeight: 500 }}>Generating your full report…</span>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.45rem", padding: "0.5rem 0.25rem" }}>
                {CHAT_STEPS.map((step, i) => {
                  const done = i < stepIndex;
                  const active = i === stepIndex;
                  if (i > stepIndex) return null; // don't reveal future steps
                  const label =
                    step.key === "run" && (done || active)
                      ? step.label + rowCountSuffix(rowCount, truncated)
                      : step.label;
                  // Derived, process-only reasoning line — shown under the ACTIVE step
                  // only, to keep completed steps to a clean checklist.
                  const reason = active
                    ? reasoningFor(step.key, { mode: routeMode, complexity, rowCount, truncated })
                    : "";
                  return (
                    <div key={step.key} style={{ display: "flex", flexDirection: "column", gap: "0.15rem" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", fontSize: "0.82rem", color: active ? t.text : t.textMuted }}>
                        {done ? (
                          <span style={{ width: 14, height: 14, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#1a9c5b", fontWeight: 700 }}>✓</span>
                        ) : (
                          <span style={{ width: 14, height: 14, border: `2px solid ${t.border}`, borderTopColor: "#d4af37", borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
                        )}
                        <span style={{ fontWeight: active ? 600 : 500 }}>{label}</span>
                      </div>
                      {reason && (
                        <span style={{ marginLeft: "1.25rem", fontSize: "0.76rem", color: t.textMuted, fontStyle: "italic", lineHeight: 1.4 }}>
                          {reason}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )
>>>>>>> origin/joelsrgv1exp1"""

c2_res = """          {loading && stepIndex >= 0 && (
            reportMode ? (
              <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", color: t.textMuted, fontSize: "0.82rem", padding: "0.5rem 0.25rem" }}>
                <span style={{ width: 14, height: 14, border: `2px solid ${t.border}`, borderTopColor: "#d4af37", borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
                <span style={{ fontWeight: 500 }}>Generating your full report…</span>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.45rem", padding: "0.5rem 0.25rem" }}>
                {CHAT_STEPS.map((step, i) => {
                  const done = i < stepIndex;
                  const active = i === stepIndex;
                  if (i > stepIndex) return null; // don't reveal future steps
                  const label =
                    step.key === "run" && (done || active)
                      ? step.label + rowCountSuffix(rowCount, truncated)
                      : step.label;
                  // Derived, process-only reasoning line — shown under the ACTIVE step
                  // only, to keep completed steps to a clean checklist.
                  const reason = active
                    ? reasoningFor(step.key, { mode: routeMode, complexity, rowCount, truncated })
                    : "";
                  return (
                    <div key={step.key} style={{ display: "flex", flexDirection: "column", gap: "0.15rem" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", fontSize: "0.82rem", color: active ? t.text : t.textMuted }}>
                        {done ? (
                          <span style={{ width: 14, height: 14, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#1a9c5b", fontWeight: 700 }}>✓</span>
                        ) : (
                          <span style={{ width: 14, height: 14, border: `2px solid ${t.border}`, borderTopColor: "#d4af37", borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
                        )}
                        <span style={{ fontWeight: active ? 600 : 500 }}>{label}</span>
                      </div>
                      {reason && (
                        <span style={{ marginLeft: "1.25rem", fontSize: "0.76rem", color: t.textMuted, fontStyle: "italic", lineHeight: 1.4 }}>
                          {reason}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )
"""

text = text.replace(c2, c2_res)

# Fix Conflict 3
c3 = """<<<<<<< HEAD
            <button
              onClick={() => handleSubmit()}
              disabled={loading || !input.trim()}
              title="Send"
              style={{
                border: "none", borderRadius: 10, width: 36, height: 36, flexShrink: 0,
                cursor: loading || !input.trim() ? "default" : "pointer",
                opacity: loading || !input.trim() ? 0.45 : 1, color: "#fff",
                background: "linear-gradient(135deg, #DB8310 0%, #F8D57C 100%)",
                display: "grid", placeItems: "center", transition: "transform 0.1s ease, box-shadow 0.15s ease",
                boxShadow: "0 2px 10px rgba(219,131,16,0.3)"
              }}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
=======
            {loading ? (
              <button
                onClick={() => abortRef.current?.abort()}
                title="Stop generating"
                style={{
                  border: "none", borderRadius: 10, width: 36, height: 36, flexShrink: 0,
                  cursor: "pointer", color: "#fff",
                  background: "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)",
                  display: "grid", placeItems: "center",
                  boxShadow: "0 2px 10px rgba(239,68,68,0.3)",
                }}
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                  <rect x="4" y="4" width="16" height="16" rx="2" />
                </svg>
              </button>
            ) : (
              <button
                onClick={() => handleSubmit()}
                disabled={!input.trim()}
                title="Send"
                style={{
                  border: "none", borderRadius: 10, width: 36, height: 36, flexShrink: 0,
                  cursor: !input.trim() ? "default" : "pointer",
                  opacity: !input.trim() ? 0.45 : 1, color: "#fff",
                  background: "linear-gradient(135deg, #d4af37 0%, #b8860b 50%, #8b6914 100%)",
                  display: "grid", placeItems: "center", transition: "transform 0.1s ease, box-shadow 0.15s ease",
                  boxShadow: "0 2px 10px rgba(212,175,55,0.3)"
                }}
              >
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            )}
>>>>>>> origin/joelsrgv1exp1"""

c3_res = """            {loading ? (
              <button
                onClick={() => abortRef.current?.abort()}
                title="Stop generating"
                style={{
                  border: "none", borderRadius: 10, width: 36, height: 36, flexShrink: 0,
                  cursor: "pointer", color: "#fff",
                  background: "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)",
                  display: "grid", placeItems: "center",
                  boxShadow: "0 2px 10px rgba(239,68,68,0.3)",
                }}
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                  <rect x="4" y="4" width="16" height="16" rx="2" />
                </svg>
              </button>
            ) : (
              <button
                onClick={() => handleSubmit()}
                disabled={!input.trim()}
                title="Send"
                style={{
                  border: "none", borderRadius: 10, width: 36, height: 36, flexShrink: 0,
                  cursor: !input.trim() ? "default" : "pointer",
                  opacity: !input.trim() ? 0.45 : 1, color: "#fff",
                  background: "linear-gradient(135deg, #DB8310 0%, #F8D57C 100%)",
                  display: "grid", placeItems: "center", transition: "transform 0.1s ease, box-shadow 0.15s ease",
                  boxShadow: "0 2px 10px rgba(219,131,16,0.3)"
                }}
              >
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            )}"""

text = text.replace(c3, c3_res)

with open('frontend-react/src/App.jsx', 'w') as f:
    f.write(text)

