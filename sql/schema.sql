-- Buddy Backend - PostgreSQL Schema
-- Safe to run on fresh DB — uses IF NOT EXISTS

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    shopify_customer_id TEXT,
    stripe_customer_id  TEXT,
    password_set    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS devices (
    id              SERIAL PRIMARY KEY,
    serial_number   TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL DEFAULT 'Buddy',
    firmware_version TEXT NOT NULL DEFAULT '4.0',
    mac_address     TEXT,
    user_id         INTEGER REFERENCES users(id),
    paired_at       TIMESTAMP,
    last_seen       TIMESTAMP,
    is_online       INTEGER DEFAULT 0,
    ip_address      TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS firmware_releases (
    id              SERIAL PRIMARY KEY,
    version         TEXT UNIQUE NOT NULL,
    changelog       TEXT,
    file_path       TEXT NOT NULL,
    file_size       INTEGER DEFAULT 0,
    critical        INTEGER DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS purchases (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    email           TEXT NOT NULL,
    shopify_order_id TEXT UNIQUE,
    product_sku     TEXT NOT NULL DEFAULT 'buddy_v1',
    product_name    TEXT NOT NULL DEFAULT 'Buddy Assistant',
    order_created_at TIMESTAMP,
    verified        INTEGER DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    date            TEXT NOT NULL,
    request_count   INTEGER DEFAULT 0,
    UNIQUE(user_id, date)
);

CREATE TABLE IF NOT EXISTS conversations (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    title           TEXT NOT NULL DEFAULT 'Nueva conversación',
    messages        TEXT NOT NULL DEFAULT '[]',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    sku             TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    features        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS animation_packs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    icon            TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    builtin         INTEGER NOT NULL DEFAULT 0,
    free            INTEGER NOT NULL DEFAULT 0,
    product_skus    TEXT NOT NULL DEFAULT '[]',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gifs            JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_purchases_email   ON purchases(email);
CREATE INDEX IF NOT EXISTS idx_purchases_user    ON purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_user      ON devices(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_serial    ON devices(serial_number);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user_date ON llm_usage(user_id, date);
