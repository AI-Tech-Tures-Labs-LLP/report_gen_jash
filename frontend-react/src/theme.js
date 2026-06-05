// Theme tokens for light/dark. Inline styles can't use CSS variables for theming
// cleanly across hover states, so we expose plain JS objects and pick by mode.

export const THEMES = {
  light: {
    bg: "#f6f7fb",
    bgPanel: "rgba(255,255,255,0.7)",
    bgCard: "rgba(255,255,255,0.85)",
    border: "rgba(15,23,42,0.10)",
    text: "#0f172a",
    textMuted: "#64748b",
    accent: "#6366f1",
    accent2: "#8b5cf6",
    userBubble: "#6366f1",
    userText: "#ffffff",
    code: "#0b1020",
    codeText: "#e2e8f0",
    shadow: "0 8px 30px rgba(2,6,23,0.08)",
  },
  dark: {
    bg: "#0b1020",
    bgPanel: "rgba(17,24,39,0.6)",
    bgCard: "rgba(30,41,59,0.55)",
    border: "rgba(148,163,184,0.18)",
    text: "#e2e8f0",
    textMuted: "#94a3b8",
    accent: "#818cf8",
    accent2: "#a78bfa",
    userBubble: "#6366f1",
    userText: "#ffffff",
    code: "#020617",
    codeText: "#e2e8f0",
    shadow: "0 8px 30px rgba(0,0,0,0.35)",
  },
};

export function getTheme(mode) {
  return THEMES[mode] || THEMES.light;
}
