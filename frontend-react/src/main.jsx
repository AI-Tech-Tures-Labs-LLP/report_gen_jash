import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import ReportPage from "./report/ReportPage.jsx";

// Lightweight routing: /report-view → the report dashboard, everything else → chat app.
function Root() {
  const path = window.location.pathname;
  if (path.startsWith("/report-view")) {
    return <ReportPage />;
  }
  return <App />;
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
