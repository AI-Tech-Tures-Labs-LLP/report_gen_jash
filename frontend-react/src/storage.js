// Conversation list persistence (localStorage), scoped per-user.
// All keys include the user ID so different users don't see each other's data.

import { getUser } from "./auth.js";

function userPrefix() {
  const user = getUser();
  const id = user?.id || "anon";
  return `sqlbot_${id}_`;
}

const CONV_SUFFIX = "conversations";
const MSG_SUFFIX = "messages_";

export function loadConversations() {
  try {
    return JSON.parse(localStorage.getItem(userPrefix() + CONV_SUFFIX) || "[]");
  } catch {
    return [];
  }
}

export function saveConversations(convs) {
  try {
    localStorage.setItem(userPrefix() + CONV_SUFFIX, JSON.stringify(convs));
  } catch {
    /* ignore */
  }
}

export function newConversationId() {
  return "conv_" + Date.now();
}

// ── Active conversation (so a page reload RESUMES the last chat instead of
//    starting a blank one). Scoped per-user like everything else. ──
const ACTIVE_SUFFIX = "active_conv";

/** Remember which conversation is currently open. */
export function saveActiveConvId(convId) {
  try {
    localStorage.setItem(userPrefix() + ACTIVE_SUFFIX, convId);
  } catch {
    /* ignore */
  }
}

/** Get the last-open conversation id, or null if none. */
export function loadActiveConvId() {
  try {
    return localStorage.getItem(userPrefix() + ACTIVE_SUFFIX) || null;
  } catch {
    return null;
  }
}

/** Save messages for a specific conversation. */
export function saveMessages(convId, messages) {
  try {
    localStorage.setItem(userPrefix() + MSG_SUFFIX + convId, JSON.stringify(messages));
  } catch {
    /* storage full — ignore */
  }
}

/** Load messages for a specific conversation. */
export function loadMessages(convId) {
  try {
    return JSON.parse(localStorage.getItem(userPrefix() + MSG_SUFFIX + convId) || "[]");
  } catch {
    return [];
  }
}

/** Delete a conversation and its messages. */
export function deleteConversation(convId) {
  try {
    localStorage.removeItem(userPrefix() + MSG_SUFFIX + convId);
    const convs = loadConversations().filter((c) => c.id !== convId);
    saveConversations(convs);
    return convs;
  } catch {
    return loadConversations();
  }
}

/** Clear all conversation data for the current user from localStorage. */
export function clearUserStorage() {
  const prefix = userPrefix();
  const keysToRemove = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && key.startsWith(prefix)) {
      keysToRemove.push(key);
    }
  }
  keysToRemove.forEach((k) => localStorage.removeItem(k));
}
