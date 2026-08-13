import { useState, useEffect, useRef } from "react";
import { login } from "./auth.js";

/**
 * LoginPage — Apple-inspired, white & gold premium sign-in page.
 * Sign-up is only accessible via /create-account URL.
 */
// Placeholder demo credentials — swap for a real demo account later.
const DEMO_EMAIL = "manager@example.com";
const DEMO_PASSWORD = "manager@example.com";

export default function LoginPage({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [showDevPanel, setShowDevPanel] = useState(false);
  const emailRef = useRef(null);
  const devPanelRef = useRef(null);

  useEffect(() => {
    setMounted(true);
    emailRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!showDevPanel) return;
    function handleClickOutside(e) {
      if (devPanelRef.current && !devPanelRef.current.contains(e.target)) {
        setShowDevPanel(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showDevPanel]);

  function handleAutofill() {
    setEmail(DEMO_EMAIL);
    setPassword(DEMO_PASSWORD);
    setError("");
    setShowDevPanel(false);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (loading) return;
    setError("");
    setLoading(true);

    try {
      await login(email.trim(), password);
      onLogin?.();
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  const isValid = email.trim() && password;

  return (
    <div style={styles.wrapper}>
      {/* Subtle animated background orbs */}
      <div style={styles.bgOrb1} />
      <div style={styles.bgOrb2} />
      <div style={styles.bgOrb3} />

      <div style={{
        ...styles.container,
        opacity: mounted ? 1 : 0,
        transform: mounted ? "translateY(0)" : "translateY(20px)",
      }}>
        {/* Logo & Branding */}
        <div style={styles.brandSection}>
          <div style={styles.logoOuter}>
            <div style={styles.logoInner}>
              <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
            </div>
          </div>
          <h1 style={styles.title}>AI SQL Analyst</h1>
          <p style={styles.subtitle}>Welcome back. Sign in to continue.</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={styles.form} autoComplete="off">
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Email</label>
            <div style={styles.inputWrapper}>
              <svg style={styles.inputIcon} viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#b86a08" strokeWidth="1.8">
                <rect x="2" y="4" width="20" height="16" rx="2" />
                <path d="M22 7l-10 7L2 7" />
              </svg>
              <input
                ref={emailRef}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                style={styles.input}
                className="login-input"
                autoComplete="off"
              />
            </div>
          </div>

          <div style={styles.fieldGroup}>
            <label style={styles.label}>Password</label>
            <div style={styles.inputWrapper} ref={devPanelRef}>
              <svg style={styles.inputIcon} viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#b86a08" strokeWidth="1.8">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                style={styles.passwordInput}
                className="login-input"
                autoComplete="new-password"
              />
              <div style={styles.inFieldActions}>
                <button
                  type="button"
                  onClick={() => setShowDevPanel((v) => !v)}
                  className="login-dev-pill"
                  style={{
                    ...styles.devPill,
                    ...(showDevPanel ? styles.devPillActive : null),
                  }}
                >
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0L19 4m-3.5 3.5L19 11" />
                  </svg>
                  Dev Login
                </button>
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  style={styles.showToggle}
                  tabIndex={-1}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>

              {showDevPanel && (
                <div style={styles.devPanel}>
                  <div style={styles.devPanelHeader}>
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#b86a08" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0L19 4m-3.5 3.5L19 11" />
                    </svg>
                    <span style={styles.devPanelTitle}>TEST CREDENTIALS</span>
                    <span style={styles.devBadge}>DEV</span>
                  </div>
                  <button
                    type="button"
                    onClick={handleAutofill}
                    className="login-dev-card"
                    style={styles.devCard}
                  >
                    <div style={styles.devAvatar}>D</div>
                    <div style={styles.devCardInfo}>
                      <div style={styles.devCardTop}>
                        <span style={styles.devCardName}>Demo Account</span>
                        <span style={styles.devCardRole}>DEMO</span>
                      </div>
                      <span style={styles.devCardEmail}>{DEMO_EMAIL}</span>
                    </div>
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#c0c7d0" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="9 18 15 12 9 6" />
                    </svg>
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* Error Message */}
          {error && (
            <div style={styles.error}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="15" y1="9" x2="9" y2="15" />
                <line x1="9" y1="9" x2="15" y2="15" />
              </svg>
              <span>{error}</span>
            </div>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            disabled={loading || !isValid}
            className="login-submit"
            style={{
              ...styles.submitBtn,
              opacity: loading || !isValid ? 0.6 : 1,
              cursor: loading || !isValid ? "default" : "pointer",
            }}
          >
            {loading ? (
              <span style={styles.spinner} />
            ) : (
              <>
                Sign In
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ marginLeft: 8 }}>
                  <line x1="5" y1="12" x2="19" y2="12" />
                  <polyline points="12 5 19 12 12 19" />
                </svg>
              </>
            )}
          </button>
        </form>
      </div>

      <style>{`
        @keyframes float1 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          33% { transform: translate(30px, -30px) scale(1.05); }
          66% { transform: translate(-20px, 20px) scale(0.95); }
        }
        @keyframes float2 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          33% { transform: translate(-40px, 20px) scale(1.08); }
          66% { transform: translate(30px, -25px) scale(0.92); }
        }
        @keyframes float3 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50% { transform: translate(25px, 25px) scale(1.04); }
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .login-input:focus {
          border-color: #DB8310 !important;
          box-shadow: 0 0 0 3px rgba(212, 175, 55, 0.12) !important;
        }
        .login-input::placeholder {
          color: #c0c7d0;
        }
        .login-submit:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 8px 30px rgba(212, 175, 55, 0.4) !important;
        }
        .login-submit:active:not(:disabled) {
          transform: translateY(0);
        }
        .login-dev-pill:hover {
          background: rgba(219,131,16,0.16) !important;
        }
        .login-dev-pill:active {
          transform: scale(0.97);
        }
        .login-dev-card:hover {
          background: rgba(219,131,16,0.09) !important;
        }
      `}</style>
    </div>
  );
}


// ─── Styles ────────────────────────────────────────────────────────────────

const styles = {
  wrapper: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "linear-gradient(135deg, #fefefe 0%, #faf8f3 30%, #f5f0e8 60%, #fefefe 100%)",
    fontFamily: "'Figtree', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    position: "relative",
    overflow: "hidden",
    padding: "2rem",
  },

  bgOrb1: {
    position: "absolute", width: 400, height: 400, borderRadius: "50%",
    background: "radial-gradient(circle, rgba(219,131,16,0.08) 0%, transparent 70%)",
    top: "-10%", right: "-5%", animation: "float1 20s ease-in-out infinite", pointerEvents: "none",
  },
  bgOrb2: {
    position: "absolute", width: 300, height: 300, borderRadius: "50%",
    background: "radial-gradient(circle, rgba(184,134,11,0.06) 0%, transparent 70%)",
    bottom: "-8%", left: "-3%", animation: "float2 25s ease-in-out infinite", pointerEvents: "none",
  },
  bgOrb3: {
    position: "absolute", width: 200, height: 200, borderRadius: "50%",
    background: "radial-gradient(circle, rgba(219,131,16,0.05) 0%, transparent 70%)",
    top: "40%", left: "60%", animation: "float3 18s ease-in-out infinite", pointerEvents: "none",
  },

  container: {
    width: "100%", maxWidth: 420,
    background: "rgba(255, 255, 255, 0.85)",
    backdropFilter: "blur(40px)", WebkitBackdropFilter: "blur(40px)",
    borderRadius: 24, border: "1px solid rgba(212, 175, 55, 0.15)",
    boxShadow: "0 4px 24px rgba(0,0,0,0.04), 0 12px 48px rgba(219,131,16,0.06)",
    padding: "2.5rem 2.25rem 2rem", position: "relative", zIndex: 1,
    transition: "opacity 0.6s cubic-bezier(0.16, 1, 0.3, 1), transform 0.6s cubic-bezier(0.16, 1, 0.3, 1)",
  },

  brandSection: { textAlign: "center", marginBottom: "1.75rem" },

  logoOuter: { display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: "1rem" },

  logoInner: {
    width: 56, height: 56,
    background: "linear-gradient(135deg, #DB8310 0%, #F8D57C 100%)",
    borderRadius: 16, display: "flex", alignItems: "center", justifyContent: "center",
    color: "#fff", boxShadow: "0 4px 20px rgba(219,131,16,0.35)",
  },

  title: {
    fontSize: "1.5rem", fontWeight: 800, margin: "0 0 0.35rem",
    background: "linear-gradient(135deg, #DB8310 0%, #F8D57C 100%)",
    WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", letterSpacing: "-0.02em",
  },

  subtitle: { fontSize: "0.88rem", color: "#8b96a3", fontWeight: 400, lineHeight: 1.5, margin: 0 },

  form: { display: "flex", flexDirection: "column", gap: "1rem" },

  fieldGroup: { display: "flex", flexDirection: "column", gap: "0.35rem" },

  label: {
    fontSize: "0.76rem", fontWeight: 600, color: "#5a6270",
    letterSpacing: "0.02em", textTransform: "uppercase", paddingLeft: 2,
  },

  inFieldActions: {
    position: "absolute", top: "50%", right: 10, transform: "translateY(-50%)",
    display: "flex", alignItems: "center", gap: "0.5rem",
  },

  devPill: {
    display: "inline-flex", alignItems: "center", gap: "0.3rem",
    padding: "0.22rem 0.55rem",
    fontSize: "0.68rem", fontWeight: 700, color: "#b86a08",
    background: "rgba(219,131,16,0.1)", border: "1px solid rgba(212, 175, 55, 0.3)",
    borderRadius: 999, cursor: "pointer", fontFamily: "inherit",
    transition: "background 0.15s, transform 0.15s", letterSpacing: "0.01em",
    whiteSpace: "nowrap",
  },

  devPillActive: {
    background: "rgba(219,131,16,0.18)",
  },

  showToggle: {
    fontSize: "0.76rem", fontWeight: 600, color: "#8b96a3",
    background: "none", border: "none", cursor: "pointer",
    fontFamily: "inherit", padding: 0,
  },

  devPanel: {
    position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 10,
    width: 260, background: "#ffffff", borderRadius: 16,
    border: "1px solid rgba(212, 175, 55, 0.2)",
    boxShadow: "0 8px 30px rgba(0,0,0,0.1), 0 2px 8px rgba(0,0,0,0.06)",
    padding: "0.6rem", animation: "fadeIn 0.15s ease both",
  },

  devPanelHeader: {
    display: "flex", alignItems: "center", gap: "0.4rem",
    padding: "0.3rem 0.4rem 0.55rem",
  },

  devPanelTitle: {
    fontSize: "0.68rem", fontWeight: 700, color: "#5a6270",
    letterSpacing: "0.04em", flex: 1,
  },

  devBadge: {
    fontSize: "0.6rem", fontWeight: 700, color: "#b86a08",
    background: "rgba(219,131,16,0.12)", borderRadius: 999,
    padding: "0.15rem 0.45rem", letterSpacing: "0.03em",
  },

  devCard: {
    width: "100%", display: "flex", alignItems: "center", gap: "0.6rem",
    padding: "0.55rem", background: "rgba(219,131,16,0.04)",
    border: "1px solid rgba(0,0,0,0.05)", borderRadius: 12,
    cursor: "pointer", textAlign: "left", fontFamily: "inherit",
    transition: "background 0.15s",
  },

  devAvatar: {
    width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
    background: "linear-gradient(135deg, #DB8310 0%, #F8D57C 100%)",
    color: "#fff", fontSize: "0.8rem", fontWeight: 700,
    display: "grid", placeItems: "center",
  },

  devCardInfo: { flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "0.15rem" },

  devCardTop: { display: "flex", alignItems: "center", gap: "0.4rem" },

  devCardName: { fontSize: "0.82rem", fontWeight: 600, color: "#1e293b" },

  devCardRole: {
    fontSize: "0.6rem", fontWeight: 700, color: "#b86a08",
    background: "rgba(219,131,16,0.1)", borderRadius: 6,
    padding: "0.1rem 0.35rem", letterSpacing: "0.02em",
  },

  devCardEmail: {
    fontSize: "0.75rem", color: "#94a3b8", whiteSpace: "nowrap",
    overflow: "hidden", textOverflow: "ellipsis",
  },

  inputWrapper: { position: "relative", display: "flex", alignItems: "center" },

  inputIcon: { position: "absolute", left: 14, pointerEvents: "none", opacity: 0.7 },

  input: {
    width: "100%", padding: "0.75rem 0.9rem 0.75rem 2.75rem",
    fontSize: "0.9rem", fontWeight: 400, color: "#1e293b",
    background: "rgba(255,255,255,0.9)", border: "1.5px solid rgba(212, 175, 55, 0.2)",
    borderRadius: 14, outline: "none", transition: "border-color 0.2s, box-shadow 0.2s",
    fontFamily: "inherit", lineHeight: 1.5,
  },

  passwordInput: {
    width: "100%", padding: "0.75rem 8.7rem 0.75rem 2.75rem",
    fontSize: "0.9rem", fontWeight: 400, color: "#1e293b",
    background: "rgba(255,255,255,0.9)", border: "1.5px solid rgba(212, 175, 55, 0.2)",
    borderRadius: 14, outline: "none", transition: "border-color 0.2s, box-shadow 0.2s",
    fontFamily: "inherit", lineHeight: 1.5,
  },

  error: {
    display: "flex", alignItems: "center", gap: "0.5rem",
    padding: "0.65rem 0.85rem", background: "rgba(239, 68, 68, 0.06)",
    border: "1px solid rgba(239, 68, 68, 0.15)", borderRadius: 12,
    color: "#dc2626", fontSize: "0.82rem", fontWeight: 500, animation: "fadeIn 0.2s ease both",
  },

  submitBtn: {
    width: "100%", padding: "0.8rem", fontSize: "0.92rem", fontWeight: 600, color: "#ffffff",
    background: "linear-gradient(135deg, #DB8310 0%, #F8D57C 100%)",
    border: "none", borderRadius: 14, display: "flex", alignItems: "center", justifyContent: "center",
    boxShadow: "0 4px 16px rgba(219,131,16,0.3)",
    transition: "all 0.2s cubic-bezier(0.16, 1, 0.3, 1)",
    fontFamily: "inherit", letterSpacing: "0.01em", marginTop: "0.25rem",
  },

  spinner: {
    width: 20, height: 20, border: "2.5px solid rgba(255,255,255,0.3)",
    borderTopColor: "#fff", borderRadius: "50%", display: "inline-block",
    animation: "spin 0.7s linear infinite",
  },
};
