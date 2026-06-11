-- Buddy Backend - Esquema completo SQLite
-- Generado desde models/database.py (fuente única de verdad)

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    shopify_customer_id TEXT,
    stripe_customer_id  TEXT,
    password_set INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_number   TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL DEFAULT 'Buddy',
    firmware_version TEXT NOT NULL DEFAULT '4.0',
    mac_address     TEXT,
    user_id         INTEGER REFERENCES users(id),
    paired_at       TEXT,
    last_seen       TEXT,
    is_online       INTEGER DEFAULT 0,
    ip_address      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS firmware_releases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         TEXT UNIQUE NOT NULL,
    changelog       TEXT,
    file_path       TEXT NOT NULL,
    file_size       INTEGER DEFAULT 0,
    critical        INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS purchases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id),
    email           TEXT NOT NULL,
    shopify_order_id TEXT UNIQUE,
    product_sku     TEXT NOT NULL DEFAULT 'buddy_v1',
    product_name    TEXT NOT NULL DEFAULT 'Buddy Assistant',
    order_created_at TEXT,
    verified        INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id),
    date            TEXT NOT NULL,
    request_count   INTEGER DEFAULT 0,
    UNIQUE(user_id, date)
);

CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id),
    title           TEXT NOT NULL DEFAULT 'Nueva conversación',
    messages        TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
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
    gif_file        TEXT,
    gif_width       INTEGER NOT NULL DEFAULT 240,
    gif_height      INTEGER NOT NULL DEFAULT 240,
    builtin         INTEGER NOT NULL DEFAULT 0,
    free            INTEGER NOT NULL DEFAULT 0,
    product_skus    TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_purchases_email   ON purchases(email);
CREATE INDEX IF NOT EXISTS idx_purchases_user    ON purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_user      ON devices(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_serial    ON devices(serial_number);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user_date ON llm_usage(user_id, date);
