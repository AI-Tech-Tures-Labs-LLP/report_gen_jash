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
    <div style={{ display: "flex", height: "100%", background: t.bg, color: t.text }}>
      {/* ── Sidebar ── */}
      {sidebarOpen && (
        <aside
          style={{
            width: 240, flexShrink: 0, background: t.bgPanel,
            borderRight: `1px solid ${t.border}`, display: "flex", flexDirection: "column",
            backdropFilter: "blur(10px)",
          }}
        >
          <div style={{ padding: "1rem", display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: `1px solid ${t.border}` }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: 700 }}>
              <span style={{ color: t.accent }}>
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
                </svg>
              </span>
              SQL Analyst
            </div>
            <button onClick={newChat} title="New chat" style={iconBtn(t)}>＋</button>
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "0.5rem" }}>
            {convs.length === 0 ? (
              <p style={{ color: t.textMuted, fontSize: "0.8rem", padding: "0.5rem" }}>No conversations yet.</p>
            ) : (
              convs.map((c) => (
                <div
                  key={c.id}
                  onClick={() => { if (c.id === convId) return; newChat(); }}
                  style={{
                    padding: "0.55rem 0.7rem", borderRadius: 8, fontSize: "0.83rem",
                    color: c.id === convId ? t.text : t.textMuted, cursor: "pointer",
                    background: c.id === convId ? t.bgCard : "transparent",
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", marginBottom: 2,
                  }}
                  title={c.title}
                >
                  {c.title}
                </div>
              ))
            )}
          </div>
        </aside>
      )}

      {/* ── Main ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* Topbar */}
        <div style={{ display: "flex", alignItems: "center", gap: "1rem", padding: "0.7rem 1rem", borderBottom: `1px solid ${t.border}`, background: t.bgPanel }}>
          <button onClick={() => setSidebarOpen((s) => !s)} title="Toggle sidebar" style={iconBtn(t)}>☰</button>
          <span style={{ fontWeight: 600, flex: 1 }}>{welcome ? "New Chat" : (convs.find((c) => c.id === convId)?.title || "Chat")}</span>
          <Switcher
            t={t}
            label="Theme"
            value={themeMode}
            options={[{ v: "light", l: "Light" }, { v: "dark", l: "Dark" }]}
            onChange={setThemeMode}
          />
        </div>

        {/* Thread */}
        <div ref={threadRef} style={{ flex: 1, overflowY: "auto", padding: "1.2rem", maxWidth: 900, width: "100%", margin: "0 auto" }}>
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
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", color: t.textMuted, fontSize: "0.85rem", padding: "0.4rem 0" }}>
              <span style={{ width: 14, height: 14, border: `2px solid ${t.border}`, borderTopColor: t.accent, borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
              {status}
            </div>
          )}
        </div>

        {/* Input */}
        <div style={{ padding: "1rem", maxWidth: 900, width: "100%", margin: "0 auto" }}>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end", background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 14, padding: "0.6rem 0.7rem", boxShadow: t.shadow }}>
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
                color: t.text, fontSize: "0.95rem", lineHeight: 1.5, maxHeight: 160,
              }}
            />
            <button
              onClick={() => handleSubmit()}
              disabled={loading || !input.trim()}
              title="Send"
              style={{
                border: "none", borderRadius: 10, width: 38, height: 38, flexShrink: 0,
                cursor: loading || !input.trim() ? "default" : "pointer",
                opacity: loading || !input.trim() ? 0.5 : 1, color: "#fff",
                background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`,
                display: "grid", placeItems: "center",
              }}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
          <p style={{ color: t.textMuted, fontSize: "0.72rem", textAlign: "center", marginTop: "0.4rem" }}>
            Press Enter to send · Shift+Enter for new line
          </p>
        </div>
      </div>
    </div>
  );
}

function Welcome({ t, onChip }) {
  return (
    <div style={{ textAlign: "center", marginTop: "8vh" }}>
      <div style={{ color: t.accent, display: "grid", placeItems: "center", marginBottom: "1rem" }}>
        <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
        </svg>
      </div>
      <h2 style={{ fontSize: "1.6rem", marginBottom: "0.5rem" }}>AI SQL Analyst</h2>
      <p style={{ color: t.textMuted, fontSize: "0.92rem", maxWidth: 520, margin: "0 auto 1.5rem", lineHeight: 1.6 }}>
        Ask anything about your data. I'll write the SQL, run it, and explain the results — or generate a full analytics report.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", justifyContent: "center", maxWidth: 640, margin: "0 auto" }}>
        {CHIPS.map((c) => (
          <button
            key={c.q}
            onClick={() => onChip(c.q)}
            style={{
              border: `1px solid ${c.report ? t.accent : t.border}`,
              background: c.report ? `${t.accent}15` : t.bgCard,
              color: c.report ? t.accent : t.text,
              borderRadius: 20, padding: "0.45rem 0.9rem", fontSize: "0.82rem", cursor: "pointer",
            }}
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
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
      <span style={{ fontSize: "0.72rem", color: t.textMuted }}>{label}</span>
      <div style={{ display: "flex", background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 8, padding: 2 }}>
        {options.map((o) => (
          <button
            key={o.v}
            onClick={() => onChange(o.v)}
            style={{
              border: "none", borderRadius: 6, padding: "0.3rem 0.7rem", fontSize: "0.78rem", cursor: "pointer",
              background: value === o.v ? `linear-gradient(135deg, ${t.accent}, ${t.accent2})` : "transparent",
              color: value === o.v ? "#fff" : t.textMuted, fontWeight: value === o.v ? 600 : 400,
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
    border: `1px solid ${t.border}`, background: t.bgCard, color: t.text,
    width: 32, height: 32, borderRadius: 8, cursor: "pointer", fontSize: "1rem",
    display: "grid", placeItems: "center",
  };
}
