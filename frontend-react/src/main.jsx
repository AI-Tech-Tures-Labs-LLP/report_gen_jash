import React, { useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import ReportPage from "./report/ReportPage.jsx";
import LoginPage from "./LoginPage.jsx";
import SignupPage from "./SignupPage.jsx";
import { isLoggedIn, validateSession, logout } from "./auth.js";

// Lightweight routing with auth gating:
//   /report-view     → report dashboard (no auth gate)
//   /create-account  → signup page (special URL, not linked from login)
//   everything else  → login page (if not authenticated) OR chat app
function Root() {
  const path = window.location.pathname;

  // Report view is always accessible (opened in new tab with localStorage data)
  if (path.startsWith("/report-view")) {
    return <ReportPage />;
  }

  // Signup page — only accessible via direct URL
  if (path === "/create-account") {
    return <SignupPage onSignup={() => { window.location.href = "/"; }} />;
  }

  // Auth-gated routes
  const [authed, setAuthed] = useState(isLoggedIn());
  const [checking, setChecking] = useState(isLoggedIn()); // only check if we have a token

  useEffect(() => {
    if (!isLoggedIn()) {
      setChecking(false);
      return;
    }
    // Validate the stored token with the backend
    validateSession().then((user) => {
      if (!user) {
        logout();
        setAuthed(false);
      }
      setChecking(false);
    });
  }, []);

  if (checking) {
    // Brief loading state while validating session
    return (
      <div style={{
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #fefefe 0%, #faf8f3 30%, #f5f0e8 60%, #fefefe 100%)",
        fontFamily: "'Figtree', sans-serif",
      }}>
        <div style={{
          width: 40, height: 40,
          border: "3px solid rgba(219,131,16,0.15)",
          borderTopColor: "#DB8310",
          borderRadius: "50%",
          animation: "spin 0.8s linear infinite",
        }} />
      </div>
    );
  }

  if (!authed) {
    return <LoginPage onLogin={() => setAuthed(true)} />;
  }

  return <App />;
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
