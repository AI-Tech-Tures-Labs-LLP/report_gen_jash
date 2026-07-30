// Authentication helper — manages JWT tokens and auth API calls.

import { apiUrl } from "./config.js";

const TOKEN_KEY = "sqlbot_auth_token";
const USER_KEY = "sqlbot_auth_user";

/** Store the JWT token and user data after login/register. */
export function setAuth(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

/** Get the stored JWT token. */
export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

/** Check if the user is logged in (has a token). */
export function isLoggedIn() {
  return !!getToken();
}

/** Get the stored user data. */
export function getUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || "null");
  } catch {
    return null;
  }
}

/** Get auth headers for API requests. */
export function getAuthHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Clear auth data and user-scoped storage (logout). */
export function logout() {
  // Clear user-scoped conversation/message cache (keyed by user ID)
  const user = getUser();
  if (user?.id) {
    const prefix = `sqlbot_${user.id}_`;
    const keysToRemove = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(prefix)) keysToRemove.push(key);
    }
    keysToRemove.forEach((k) => localStorage.removeItem(k));
  }
  // Also clear any report data in localStorage
  const reportKeys = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && key.startsWith("sqlbot_report_")) reportKeys.push(key);
  }
  reportKeys.forEach((k) => localStorage.removeItem(k));

  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

/** Register a new user. Returns { token, user }. */
export async function register(email, password, name) {
  const res = await fetch(apiUrl("/auth/register"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, name }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Registration failed");
  setAuth(data.token, data.user);
  return data;
}

/** Log in an existing user. Returns { token, user }. */
export async function login(email, password) {
  const res = await fetch(apiUrl("/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Login failed");
  setAuth(data.token, data.user);
  return data;
}

/** Validate the current session. Returns user data or null. */
export async function validateSession() {
  const token = getToken();
  if (!token) return null;
  try {
    const res = await fetch(apiUrl("/auth/me"), {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      logout();
      return null;
    }
    const user = await res.json();
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    return user;
  } catch {
    return null;
  }
}
