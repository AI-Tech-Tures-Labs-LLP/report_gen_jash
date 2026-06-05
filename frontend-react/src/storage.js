// Conversation list persistence (localStorage), mirroring the vanilla app.

const KEY = "sqlbot_conversations";

export function loadConversations() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

export function saveConversations(convs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(convs));
  } catch {
    /* ignore */
  }
}

export function newConversationId() {
  return "conv_" + Date.now();
}
