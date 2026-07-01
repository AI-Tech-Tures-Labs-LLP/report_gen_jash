import { useState, useRef, useEffect } from "react";
import { getTheme } from "./theme.js";
import { askStream, openReport } from "./api.js";
import { loadConversations, saveConversations, newConversationId, saveMessages, loadMessages, deleteConversation, saveActiveConvId, loadActiveConvId } from "./storage.js";
import { getAuthHeaders, logout, getUser } from "./auth.js";
import { CHAT_STEPS, MIN_STEP_MS, stepIndexForStage, rowCountSuffix, reasoningFor } from "./progressSteps.js";
import ChatMessage from "./components/ChatMessage.jsx";
import ReportOffer from "./components/ReportOffer.jsx";
import ReportSuccess from "./components/ReportSuccess.jsx";

const CHIPS = [
  { q: "What is the total revenue this year?", label: "Total revenue this year" },
  { q: "Top 10 customers by revenue", label: "Top 10 customers" },
  { q: "Which vendor has the highest purchase order value?", label: "Top vendor by PO value" },
  { q: "What is the average order value?", label: "Average order value" },
  { q: "Generate a sales performance report", label: "Sales Performance Report", report: true },
  { q: "Generate a gold products analysis report", label: "Gold Products Analysis", report: true },
];

export default function App() {
  const [themeMode, setThemeMode] = useState(
    () => localStorage.getItem("sqlbot_theme") || "light"
  );
  const t = getTheme(themeMode);

  const [messages, setMessages] = useState([]); // {id, role, text?, data?, error?, showOffer?, question?}
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  // Curated live progression (see progressSteps.js). `stepIndex` is how far through
  // CHAT_STEPS we've advanced; `rowCount` populates the "N rows found" trust signal.
  const [stepIndex, setStepIndex] = useState(-1);
  const [rowCount, setRowCount] = useState(null);
  const [truncated, setTruncated] = useState(false); // result set capped by backend
  const [routeMode, setRouteMode] = useState(null);   // router mode (data/report/...)
  const [complexity, setComplexity] = useState(null); // router complexity (simple/complex)
  const [reportMode, setReportMode] = useState(false); // report intent → different copy
  // Pacing refs: enforce a minimum visible duration per step so fast queries don't strobe.
  const stepRef = useRef(-1);        // highest step reached (may be ahead of what's shown)
  const shownStepRef = useRef(-1);   // step currently rendered
  const lastAdvanceRef = useRef(0);  // timestamp of the last visible advance
  const paceTimerRef = useRef(null); // pending delayed advance
  // Resume the last-open conversation on reload; only mint a fresh one if there's none.
  const [convId, setConvId] = useState(() => loadActiveConvId() || newConversationId());
  const [convs, setConvs] = useState(() => loadConversations());
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const threadRef = useRef(null);
  const textareaRef = useRef(null);
  const syncTimer = useRef(null); // debounce MongoDB sync
  const isLoadingConv = useRef(false); // prevent sync during conversation switch
  const abortRef = useRef(null);

  useEffect(() => {
    localStorage.setItem("sqlbot_theme", themeMode);
    document.documentElement.setAttribute("data-theme", themeMode);
  }, [themeMode]);

  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [messages, stepIndex]);

  // Persist messages whenever they change (skip empty — that's a new chat)
  useEffect(() => {
    if (messages.length === 0) return;
    if (isLoadingConv.current) return; // don't re-sync while switching conversations

    saveMessages(convId, messages);

    // Debounce MongoDB sync (300ms) to coalesce rapid message updates
    if (syncTimer.current) clearTimeout(syncTimer.current);
    syncTimer.current = setTimeout(() => {
      const title = convs.find((c) => c.id === convId)?.title || "Chat";
      syncToMongo(convId, title, messages);
    }, 300);
  }, [messages, convId]);

  // Remember the active conversation so a page reload RESUMES it (instead of
  // dropping into a blank new chat). Read back in the convId useState initializer.
  useEffect(() => {
    saveActiveConvId(convId);
  }, [convId]);

  // On mount: restore the resumed conversation's messages (instant, from localStorage)
  // so the screen isn't blank after a reload. initConversations() refreshes the list.
  useEffect(() => {
    const saved = loadMessages(convId);
    if (saved.length > 0) setMessages(saved);
    initConversations();
  }, []);

  async function initConversations() {
    try {
      const res = await fetch("/conversations", { headers: getAuthHeaders() });
      if (!res.ok) return;
      const mongoConvs = await res.json();

      if (mongoConvs.length > 0) {
        // MongoDB has data — use it as the source of truth
        const mapped = mongoConvs.map((c) => ({ id: c.conv_id, title: c.title }));
        setConvs(mapped);
        saveConversations(mapped);
      } else {
        // MongoDB is empty — migrate existing localStorage conversations
        const localConvs = loadConversations();
        if (localConvs.length > 0) {
          console.log(`Migrating ${localConvs.length} conversations to MongoDB...`);
          for (const c of localConvs) {
            const msgs = loadMessages(c.id);
            if (msgs.length > 0) {
              await syncToMongo(c.id, c.title, msgs);
            }
          }
          console.log("Migration complete.");
        }
      }
    } catch (err) {
      console.warn("Failed to init conversations from MongoDB:", err);
    }
  }

  async function syncToMongo(cId, title, msgs) {
    try {
      const res = await fetch("/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ conv_id: cId, title, messages: msgs }),
      });
      if (!res.ok) console.warn("Failed to sync conversation to MongoDB:", res.status);
    } catch (err) {
      console.warn("Sync to MongoDB failed:", err);
    }
  }

  async function deleteFromMongo(cId) {
    try {
      const res = await fetch(`/conversations/${cId}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      if (!res.ok) console.warn("Failed to delete conversation from MongoDB:", res.status);
    } catch (err) {
      console.warn("Delete from MongoDB failed:", err);
    }
  }

  function pushMessage(m) {
    setMessages((prev) => [...prev, { id: Date.now() + Math.random(), ...m }]);
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
  }

  async function handleSubmit(question) {
    const q = (question ?? input).trim();
    if (!q || loading) return;
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setLoading(true);
    resetProgress();
    stepRef.current = 0;          // start on "Understanding your question"
    shownStepRef.current = 0;
    lastAdvanceRef.current = Date.now();
    setStepIndex(0);
    pushMessage({ role: "user", text: q, ts: new Date().toISOString() });

    // register conversation in sidebar
    setConvs((prev) => {
      if (prev.find((c) => c.id === convId)) return prev;
      const next = [{ id: convId, title: q.slice(0, 40) }, ...prev];
      saveConversations(next);
      return next;
    });

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const finalData = await askStream(q, convId, (event) => {
        // Report intent uses a different (single-step) copy — the report pipeline
        // doesn't emit the chat sql/execute/interpret stages.
        if (event.stage === "routed") {
          // Capture the real routing decision — drives the derived reasoning copy.
          if (event.data?.mode) setRouteMode(event.data.mode);
          if (event.data?.complexity) setComplexity(event.data.complexity);
          if (event.data?.mode === "report") { setReportMode(true); return; }
        }
        if (event.stage === "report_generating") {
          setReportMode(true);
          return;
        }
        // Capture the row count + truncation off the execute stage for the "N rows" signal.
        if (event.stage === "execute") {
          if (typeof event.data?.row_count === "number") setRowCount(event.data.row_count);
          if (event.data?.truncated) setTruncated(true);
        }
        // Map the backend stage to a curated step and advance (paced) toward it.
        const idx = stepIndexForStage(event.stage);
        if (idx > stepRef.current) {
          stepRef.current = idx;
          paceToStep(idx);
        }
      }, controller.signal);

      if (finalData && finalData.mode === "report" && finalData.report) {
        // Report intent → the full report is ALREADY generated. Store it once and
        // show an "Open Report" card that just re-opens the stored tab (no regen).
        const reportId = openReport(q, finalData, convId);
        pushMessage({ role: "ai", reportId, ts: new Date().toISOString() });
      } else if (finalData) {
        // Only offer "Generate Report" for real DATA answers. Non-data turns
        // (greeting/off-topic/refused) set report_eligible=false / non_data=true,
        // so no report card appears for them.
        const offerReport = finalData.report_eligible !== false && !finalData.non_data;
        pushMessage({ role: "ai", data: finalData, showOffer: offerReport, question: q, ts: new Date().toISOString() });
      } else {
        pushMessage({ role: "ai", error: "No response received from server." });
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        pushMessage({ role: "ai", error: err.message || "Something went wrong.", ts: new Date().toISOString() });
      }
    } finally {
      setLoading(false);
      resetProgress();
      abortRef.current = null;
    }
  }

  function newChat() {
    setMessages([]);
    setConvId(newConversationId());
  }

  function switchConversation(targetConvId) {
    if (targetConvId === convId) return;
    // Try loading from MongoDB first, fallback to localStorage
    loadConvFromMongo(targetConvId);
  }

  async function loadConvFromMongo(targetConvId) {
    isLoadingConv.current = true; // prevent sync useEffect from firing during load
    try {
      const res = await fetch(`/conversations/${targetConvId}`, { headers: getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        if (data.messages && data.messages.length > 0) {
          setConvId(targetConvId);
          setMessages(data.messages);
          saveMessages(targetConvId, data.messages);
          return;
        }
      }
    } catch (err) {
      console.warn("Failed to load conversation from MongoDB:", err);
    }
    // Fallback to localStorage
    const saved = loadMessages(targetConvId);
    setConvId(targetConvId);
    setMessages(saved);
    // Use setTimeout to re-enable sync after React commits the state update
  } 

  // Re-enable sync after loading completes (runs after state settles)
  useEffect(() => {
    if (isLoadingConv.current) {
      // Allow one render cycle, then re-enable sync
      const t = setTimeout(() => { isLoadingConv.current = false; }, 100);
      return () => clearTimeout(t);
    }
  }, [convId]);

  async function handleDeleteConversation(e, targetConvId) {
    e.stopPropagation();

    // Clean up any report data in localStorage for this conversation
    const msgs = loadMessages(targetConvId);
    for (const m of msgs) {
      if (m.reportId) {
        localStorage.removeItem("sqlbot_report_" + m.reportId);
        localStorage.removeItem("sqlbot_report_" + m.reportId + "_question");
        localStorage.removeItem("sqlbot_report_" + m.reportId + "_theme");
      }
    }

    // Remove from localStorage (conversations list + messages)
    const remaining = deleteConversation(targetConvId);
    setConvs(remaining);

    // Remove from MongoDB (cascades: deletes conversation + all its reports)
    await deleteFromMongo(targetConvId);

    // If deleting the active conversation, start a new chat
    if (targetConvId === convId) {
      newChat();
    }
  }

  function handleSignOut() {
    logout();
    window.location.href = "/";
  }

  const welcome = messages.length === 0;

  return (
    <div style={{ display: "flex", height: "100%", background: t.bg, color: t.text, fontFamily: "'Inter', sans-serif" }}>
      {/* ── Sidebar ── */}
      {sidebarOpen && (
        <aside
          style={{
            width: 250, flexShrink: 0, background: t.bgPanel,
            borderRight: `1px solid ${t.border}`, display: "flex", flexDirection: "column",
            backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)",
            transition: "width 0.3s ease",
          }}
        >
          <div style={{ padding: "0.85rem 1rem", height: 50, display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: `1px solid ${t.border}` }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", fontWeight: 700, fontSize: "0.92rem", color: t.text }}>
              <div style={{
                width: 28, height: 28, background: "linear-gradient(135deg, #d4af37 0%, #b8860b 50%, #8b6914 100%)",
                borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center", color: "#fff",
                boxShadow: "0 2px 8px rgba(212,175,55,0.3)"
              }}>
                <svg viewBox="0 0 24 24" width="16" height="18" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
                </svg>
              </div>
              <span>SQL Analyst</span>
            </div>
            <button onClick={newChat} title="New chat" style={iconBtn(t)}>+</button>
          </div>
          <div className="sidebar-list" style={{ flex: 1, overflowY: "auto", padding: "0.75rem 0.6rem" }}>
            <div style={{ fontSize: "0.65rem", fontWeight: 700, color: t.textMuted, textTransform: "uppercase", letterSpacing: "0.08em", padding: "0 0.4rem 0.4rem" }}>Conversations</div>
            {convs.length === 0 ? (
              <p style={{ color: t.textMuted, fontSize: "0.78rem", padding: "1rem 0.4rem", textAlign: "center" }}>No chats yet.</p>
            ) : (
              convs.map((c) => {
                const isActive = c.id === convId;
                return (
                  <div
                    key={c.id}
                    onClick={() => switchConversation(c.id)}
                    className="sidebar-conv-item"
                    style={{
                      padding: "0.6rem 0.75rem", paddingRight: "2rem", borderRadius: 8, fontSize: "0.8rem",
                      fontWeight: isActive ? 600 : 500,
                      color: isActive ? "#b8860b" : t.textMuted, cursor: "pointer",
                      background: isActive ? "linear-gradient(135deg, rgba(212, 175, 55, 0.12) 0%, rgba(184, 134, 11, 0.08) 100%)" : "transparent",
                      border: isActive ? "1px solid rgba(212, 175, 55, 0.3)" : "1px solid transparent",
                      whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", marginBottom: 3,
                      position: "relative",
                      transition: "all 0.15s ease",
                    }}
                    title={c.title}
                  >
                    {isActive && (
                      <div style={{ position: "absolute", left: 0, top: "20%", bottom: "20%", width: 3, background: "linear-gradient(135deg,#d4af37 0%,#b8860b 100%)", borderRadius: "0 3px 3px 0" }} />
                    )}
                    {c.title}
                    <button
                      onClick={(e) => handleDeleteConversation(e, c.id)}
                      className="sidebar-delete-btn"
                      title="Delete conversation"
                      style={{
                        position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)",
                        background: "none", border: "none", cursor: "pointer",
                        color: t.textMuted, padding: 4, borderRadius: 6,
                        opacity: 0, transition: "opacity 0.15s, color 0.15s",
                        display: "grid", placeItems: "center",
                      }}
                    >
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                  </div>
                );
              })
            )}
          </div>

          {/* Sign Out Button */}
          <div style={{ padding: "0.6rem", borderTop: `1px solid ${t.border}` }}>
            <button
              onClick={handleSignOut}
              className="sidebar-signout-btn"
              style={{
                width: "100%", padding: "0.55rem 0.75rem", borderRadius: 8,
                border: `1px solid ${t.border}`, background: "transparent",
                color: t.textMuted, fontSize: "0.78rem", fontWeight: 500,
                cursor: "pointer", display: "flex", alignItems: "center",
                gap: "0.5rem", justifyContent: "center",
                transition: "all 0.15s ease", fontFamily: "inherit",
              }}
            >
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              Sign Out
            </button>
          </div>
        </aside>
      )}

      {/* ── Main ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, position: "relative" }}>
        {/* Topbar */}
        <div style={{ display: "flex", alignItems: "center", gap: "1rem", padding: "0 1.25rem", height: 50, borderBottom: `1px solid ${t.border}`, background: t.bgPanel, backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", zIndex: 10 }}>
          <button onClick={() => setSidebarOpen((s) => !s)} title="Toggle sidebar" style={iconBtn(t)}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><line x1="2" y1="4" x2="14" y2="4"/><line x1="2" y1="8" x2="14" y2="8"/><line x1="2" y1="12" x2="14" y2="12"/></svg>
          </button>
          <span style={{ fontWeight: 600, fontSize: "0.88rem", flex: 1, color: t.text }}>{welcome ? "New Chat" : (convs.find((c) => c.id === convId)?.title || "Chat")}</span>
          <Switcher
            t={t}
            label="Theme"
            value={themeMode}
            options={[{ v: "light", l: "Light" }, { v: "dark", l: "Dark" }]}
            onChange={setThemeMode}
          />
        </div>

        {/* Thread */}
        <div ref={threadRef} style={{ flex: 1, overflowY: "auto", padding: "1.5rem", maxWidth: 840, width: "100%", margin: "0 auto", position: "relative" }}>
          {welcome ? (
            <Welcome t={t} onChip={(q) => handleSubmit(q)} />
          ) : (
            messages.map((m) => (
              <div key={m.id}>
                {m.reportId ? (
                  <ReportSuccess reportId={m.reportId} t={t} />
                ) : (
                  <>
                    <ChatMessage msg={m} t={t} themeMode={themeMode} />
                    {m.showOffer && <ReportOffer question={m.question} t={t} convId={convId} />}
                  </>
                )}
              </div>
            ))
          )}
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
          )}
        </div>

        {/* Input */}
        <div style={{ padding: "0.75rem 1.5rem 1rem", maxWidth: 840, width: "100%", margin: "0 auto", flexShrink: 0 }}>
          <div style={{
            display: "flex", gap: "0.6rem", alignItems: "flex-end", background: t.bgCard,
            border: `1px solid ${t.border}`, borderRadius: 16, padding: "0.55rem 0.65rem 0.55rem 1rem",
            boxShadow: t.shadow, backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)",
            transition: "border-color 0.15s, box-shadow 0.15s",
          }} className="input-box-focus">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                const el = e.target;
                el.style.height = "auto";
                el.style.height = Math.min(el.scrollHeight, 160) + "px";
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
              }}
              rows={1}
              placeholder="Ask a question about your data…"
              spellCheck={false}
              style={{
                flex: 1, resize: "none", border: "none", outline: "none", background: "transparent",
                color: t.text, fontSize: "0.92rem", lineHeight: 1.55,
                maxHeight: 160, overflowY: "auto", padding: "0.4rem 0",
              }}
            />
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
          </div>
          <p style={{ color: t.textMuted, fontSize: "0.68rem", textAlign: "center", marginTop: "0.45rem", letterSpacing: "0.01em" }}>
            Press Enter to send · Shift+Enter for new line
          </p>
        </div>
      </div>
    </div>
  );
}

function Welcome({ t, onChip }) {
  return (
    <div style={{ textAlign: "center", marginTop: "8vh", animation: "fadeIn 0.3s ease both" }}>
      <div style={{ display: "grid", placeItems: "center", marginBottom: "1.25rem" }}>
        <div style={{
          width: 60, height: 60,
          background: "linear-gradient(135deg, #d4af37 0%, #b8860b 50%, #8b6914 100%)",
          borderRadius: 14, display: "flex", color: "#fff",
          boxShadow: "0 4px 20px rgba(212,175,55,0.35)",
          alignItems: "center", justifyContent: "center",
        }}>
          <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
          </svg>
        </div>
      </div>
      <h2 style={{ fontSize: "1.65rem", fontWeight: 800, marginBottom: "0.6rem", background: "linear-gradient(135deg, #d4af37 0%, #b8860b 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>AI SQL Analyst</h2>
      <p style={{ color: t.textMuted, fontSize: "0.9rem", maxWidth: 480, margin: "0 auto 1.75rem", lineHeight: 1.65 }}>
        Ask anything about your data. I'll write the SQL, run it, and explain the results — or generate a full analytics report.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", justifyContent: "center", maxWidth: 640, margin: "0 auto" }}>
        {CHIPS.map((c) => (
          <button
            key={c.q}
            onClick={() => onChip(c.q)}
            style={{
              border: `1px solid ${c.report ? "rgba(212, 175, 55, 0.4)" : t.border}`,
              background: c.report ? "rgba(212, 175, 55, 0.06)" : t.bgCard,
              color: c.report ? "#b8860b" : t.text,
              borderRadius: 20, padding: "0.48rem 1rem", fontSize: "0.8rem", cursor: "pointer",
              fontWeight: 500, boxShadow: "0 1px 3px rgba(0,0,0,0.02)",
              transition: "all 0.15s ease",
            }}
            className="rpt-welcome-chip"
          >
            {c.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Switcher({ t, label, value, options, onChange }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
      <span style={{ fontSize: "0.72rem", color: t.textMuted, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>{label}</span>
      <div style={{ display: "flex", background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 20, padding: 3 }}>
        {options.map((o) => (
          <button
            key={o.v}
            onClick={() => onChange(o.v)}
            style={{
              border: "none", borderRadius: 16, padding: "0.35rem 0.85rem", fontSize: "0.76rem", cursor: "pointer",
              background: value === o.v ? "linear-gradient(135deg, #d4af37 0%, #b8860b 100%)" : "transparent",
              color: value === o.v ? "#fff" : t.textMuted, fontWeight: value === o.v ? 600 : 400,
              boxShadow: value === o.v ? "0 2px 8px rgba(212,175,55,0.25)" : "none",
              transition: "all 0.15s ease",
            }}
          >
            {o.l}
          </button>
        ))}
      </div>
    </div>
  );
}

function iconBtn(t) {
  return {
    border: `1px solid ${t.border}`, background: t.bgCard, color: t.textMuted,
    width: 32, height: 32, borderRadius: 8, cursor: "pointer", fontSize: "1rem",
    display: "grid", placeItems: "center", transition: "all 0.15s ease",
  };
}
