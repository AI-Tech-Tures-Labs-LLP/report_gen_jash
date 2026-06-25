// Theme tokens for light/dark. Inline styles can't use CSS variables for theming
// cleanly across hover states, so we expose plain JS objects and pick by mode.

export const THEMES = {
  light: {
    bg: "#f1f5f9",
    bgPanel: "rgba(255,255,255,0.95)",
    bgCard: "rgba(255,255,255,0.95)",
    border: "rgba(219,131,16,0.25)",
    text: "#1e293b",
    textMuted: "#94a3b8",
    accent: "#DB8310",
    accent2: "#b86a08",
    userBubble: "linear-gradient(135deg, #DB8310 0%, #F8D57C 100%)",
    userText: "#ffffff",
    code: "#0f172a",
    codeText: "#f8fafc",
    shadow: "0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(219,131,16,0.06)",
  },
  dark: {
    bg: "#0d1117",
    bgPanel: "rgba(13,17,23,0.92)",
    bgCard: "rgba(22,27,34,0.92)",
    border: "rgba(219,131,16,0.14)",
    text: "#e6edf3",
    textMuted: "#8d96a0",
    accent: "#DB8310",
    accent2: "#b86a08",
    userBubble: "linear-gradient(135deg, #DB8310 0%, #F8D57C 100%)",
    userText: "#ffffff",
    code: "#0f172a",
    codeText: "#f8fafc",
    shadow: "0 1px 3px rgba(0,0,0,0.3), 0 4px 16px rgba(0,0,0,0.25)",
  },
};

export function getTheme(mode) {
  return THEMES[mode] || THEMES.light;
}
