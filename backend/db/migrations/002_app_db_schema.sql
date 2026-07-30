-- Migration 002: app-owned data — runs against the APP database
-- (stacklogix_reportgeneration), NOT the read-only analytics DB.
--
-- Replaces MongoDB (users / conversations / reports) and the two app tables
-- that were previously created inside V2_inventory_management
-- (system_cache, chat_history).

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ── Users ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Case-insensitive uniqueness: emails are stored lowercased by the app, but
-- enforce it in the DB so a bypass can't create duplicate accounts.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (LOWER(email));

-- ── Conversations ───────────────────────────────────────────────────────────
-- conv_id is the client-generated id used in URLs; unique per user.
CREATE TABLE IF NOT EXISTS conversations (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    conv_id    TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT 'New chat',
    messages   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, conv_id)
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
    ON conversations (user_id, updated_at DESC);

-- ── Conversation turns (the AI's working memory) ─────────────────────────────
-- Was the `turns` array on the Mongo conversation doc. A real table so
-- get_recent_turns can LIMIT server-side instead of slicing an array.
CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id      BIGSERIAL PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    conv_id      TEXT NOT NULL,
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL,
    sql          TEXT NOT NULL DEFAULT '',
    query_result JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_turns_user_conv
    ON conversation_turns (user_id, conv_id, turn_id);

-- ── Reports ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reports (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    report_id   TEXT NOT NULL,
    conv_id     TEXT,
    question    TEXT NOT NULL DEFAULT '',
    report_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, report_id)
);

CREATE INDEX IF NOT EXISTS idx_reports_user_created
    ON reports (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_user_conv
    ON reports (user_id, conv_id);

-- ── System cache (was created inside the analytics DB by db/profiler.py) ─────
-- cache_value is TEXT, not JSONB: profiler stores a pre-formatted prompt string.
CREATE TABLE IF NOT EXISTS system_cache (
    cache_key   VARCHAR(100) PRIMARY KEY,
    cache_value TEXT NOT NULL,
    built_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Migration tracking ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(10) PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_migrations (version, description)
VALUES ('002', 'App DB: users, conversations, conversation_turns, reports, system_cache')
ON CONFLICT (version) DO NOTHING;

COMMIT;
