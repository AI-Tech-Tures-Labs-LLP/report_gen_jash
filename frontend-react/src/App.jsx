import { useState, useRef, useEffect } from "react";
import { getTheme } from "./theme.js";
import { askStream, openReport } from "./api.js";
import { loadConversations, saveConversations, newConversationId } from "./storage.js";
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
  const [status, setStatus] = useState(""); // streaming status line
  const [convId, setConvId] = useState(() => newConversationId());
  const [convs, setConvs] = useState(() => loadConversations());
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const threadRef = useRef(null);

  useEffect(() => {
    localStorage.setItem("sqlbot_theme", themeMode);
    document.documentElement.setAttribute("data-theme", themeMode);
  }, [themeMode]);

  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [messages, status]);

  function pushMessage(m) {
    setMessages((prev) => [...prev, { id: Date.now() + Math.random(), ...m }]);
  }

  async function handleSubmit(question) {
    const q = (question ?? input).trim();
    if (!q || loading) return;
    setInput("");
    setLoading(true);
    setStatus("Understanding your request…");
    pushMessage({ role: "user", text: q });

    // register conversation in sidebar
    setConvs((prev) => {
      if (prev.find((c) => c.id === convId)) return prev;
      const next = [{ id: convId, title: q.slice(0, 40) }, ...prev];
      saveConversations(next);
      return next;
    });

    try {
      const finalData = await askStream(q, convId, (event) => {
        if (event.stage === "routed") {
          setStatus(event.data?.mode === "report" ? "Generating full report…" : "Finding your answer…");
        } else if (event.stage !== "complete") {
          setStatus(event.data?.message || event.text || "Working…");
        }
      });

      if (finalData && finalData.mode === "report" && finalData.report) {
        // Report intent → the full report is ALREADY generated. Store it once and
        // show an "Open Report" card that just re-opens the stored tab (no regen).
        const reportId = openReport(q, finalData);
        pushMessage({ role: "ai", reportId });
      } else if (finalData) {
        pushMessage({ role: "ai", data: finalData, showOffer: true, question: q });
      } else {
        pushMessage({ role: "ai", error: "No response received from server." });
      }
    } catch (err) {
      pushMessage({ role: "ai", error: err.message || "Something went wrong." });
    } finally {
      setLoading(false);
      setStatus("");
    }
  }

  function newChat() {
    setMessages([]);
    setConvId(newConversationId());
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
                    onClick={() => { if (isActive) return; newChat(); }}
                    style={{
                      padding: "0.6rem 0.75rem", borderRadius: 8, fontSize: "0.8rem",
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
                  </div>
                );
              })
            )}
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
                    <ChatMessage msg={m} t={t} />
                    {m.showOffer && <ReportOffer question={m.question} t={t} />}
                  </>
                )}
              </div>
            ))
          )}
          {loading && status && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", color: t.textMuted, fontSize: "0.82rem", padding: "0.5rem 0.25rem" }}>
              <span style={{ width: 14, height: 14, border: `2px solid ${t.border}`, borderTopColor: "#d4af37", borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
              <span style={{ fontWeight: 500 }}>{status}</span>
            </div>
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
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
              }}
              rows={1}
              placeholder="Ask a question about your data…"
              spellCheck={false}
              style={{
                flex: 1, resize: "none", border: "none", outline: "none", background: "transparent",
                color: t.text, fontSize: "0.92rem", lineHeight: 1.55, maxHeight: 140, padding: "0.4rem 0",
              }}
            />
            <button
              onClick={() => handleSubmit()}
              disabled={loading || !input.trim()}
              title="Send"
              style={{
                border: "none", borderRadius: 10, width: 36, height: 36, flexShrink: 0,
                cursor: loading || !input.trim() ? "default" : "pointer",
                opacity: loading || !input.trim() ? 0.45 : 1, color: "#fff",
                background: "linear-gradient(135deg, #d4af37 0%, #b8860b 50%, #8b6914 100%)",
                display: "grid", placeItems: "center", transition: "transform 0.1s ease, box-shadow 0.15s ease",
                boxShadow: "0 2px 10px rgba(212,175,55,0.3)"
              }}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
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
