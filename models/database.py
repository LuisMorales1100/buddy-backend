import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "..", "buddy.db"))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            shopify_customer_id TEXT,
            stripe_customer_id TEXT
        );

        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serial_number TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL DEFAULT 'Buddy',
            firmware_version TEXT NOT NULL DEFAULT '4.0',
            mac_address TEXT,
            user_id INTEGER REFERENCES users(id),
            paired_at TEXT,
            last_seen TEXT,
            is_online INTEGER DEFAULT 0,
            ip_address TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS firmware_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT UNIQUE NOT NULL,
            changelog TEXT,
            file_path TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            critical INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            email TEXT NOT NULL,
            shopify_order_id TEXT UNIQUE,
            product_sku TEXT NOT NULL DEFAULT 'buddy_v1',
            product_name TEXT NOT NULL DEFAULT 'Buddy Assistant',
            order_created_at TEXT,
            verified INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            date TEXT NOT NULL,
            request_count INTEGER DEFAULT 0,
            UNIQUE(user_id, date)
        );

        CREATE INDEX IF NOT EXISTS idx_purchases_email ON purchases(email);
        CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases(user_id);
        CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
        CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial_number);

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            title TEXT NOT NULL DEFAULT 'Nueva conversación',
            messages TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
    """)
    conn.commit()
    conn.close()
    print(f"[DB] Initialized at {DB_PATH}")


class UserModel:
    @staticmethod
    def create(email: str, password_hash: str, name: str = "") -> Optional[int]:
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
                (email, password_hash, name),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()

    @staticmethod
    def get_by_email(email: str) -> Optional[dict]:
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def get_by_id(user_id: int) -> Optional[dict]:
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def set_shopify_id(user_id: int, shopify_id: str):
        conn = get_db()
        conn.execute("UPDATE users SET shopify_customer_id = ? WHERE id = ?", (shopify_id, user_id))
        conn.commit()
        conn.close()


class DeviceModel:
    @staticmethod
    def register(serial: str, name: str, fw_version: str, mac: str) -> bool:
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO devices (serial_number, name, firmware_version, mac_address) VALUES (?, ?, ?, ?)",
                (serial, name, fw_version, mac),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def get_by_serial(serial: str) -> Optional[dict]:
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM devices WHERE serial_number = ?", (serial,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def pair(serial: str, user_id: int) -> bool:
        conn = get_db()
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE devices SET user_id = ?, paired_at = ? WHERE serial_number = ?",
            (user_id, now, serial),
        )
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def get_user_devices(user_id: int) -> list[dict]:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM devices WHERE user_id = ? ORDER BY last_seen DESC",
            (user_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def update_last_seen(serial: str, ip: str, online: bool = True):
        conn = get_db()
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE devices SET last_seen = ?, ip_address = ?, is_online = ? WHERE serial_number = ?",
            (now, ip, int(online), serial),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def set_firmware_version(serial: str, version: str):
        conn = get_db()
        conn.execute(
            "UPDATE devices SET firmware_version = ? WHERE serial_number = ?",
            (version, serial),
        )
        conn.commit()
        conn.close()


class PurchaseModel:
    @staticmethod
    def create(email: str, shopify_order_id: str, product_sku: str = "buddy_v1", product_name: str = "Buddy Assistant") -> bool:
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO purchases (email, shopify_order_id, product_sku, product_name, order_created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                (email, shopify_order_id, product_sku, product_name),
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"[Purchase] Error: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def link_to_user(email: str, user_id: int):
        conn = get_db()
        conn.execute("UPDATE purchases SET user_id = ?, verified = 1 WHERE email = ? AND user_id IS NULL", (user_id, email))
        conn.commit()
        conn.close()

    @staticmethod
    def user_has_purchase(user_id: int) -> bool:
        conn = get_db()
        row = conn.execute(
            "SELECT id FROM purchases WHERE user_id = ? AND verified = 1 LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()
        return row is not None

    @staticmethod
    def get_by_email(email: str) -> Optional[dict]:
        conn = get_db()
        row = conn.execute("SELECT * FROM purchases WHERE email = ? LIMIT 1", (email,)).fetchone()
        conn.close()
        return dict(row) if row else None


class UsageModel:
    DAILY_LIMIT_FREE = 20
    DAILY_LIMIT_PAID = 999999

    @staticmethod
    def get_daily_count(user_id: int) -> int:
        conn = get_db()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT request_count FROM llm_usage WHERE user_id = ? AND date = ?",
            (user_id, today),
        ).fetchone()
        conn.close()
        return row["request_count"] if row else 0

    @staticmethod
    def increment(user_id: int) -> int:
        conn = get_db()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO llm_usage (user_id, date, request_count) VALUES (?, ?, 1) ON CONFLICT(user_id, date) DO UPDATE SET request_count = request_count + 1",
            (user_id, today),
        )
        conn.commit()
        row = conn.execute(
            "SELECT request_count FROM llm_usage WHERE user_id = ? AND date = ?",
            (user_id, today),
        ).fetchone()
        conn.close()
        return row["request_count"] if row else 0

    @staticmethod
    def can_make_request(user_id: int) -> tuple[bool, str]:
        daily = UsageModel.get_daily_count(user_id)
        has_purchase = PurchaseModel.user_has_purchase(user_id)
        limit = UsageModel.DAILY_LIMIT_PAID if has_purchase else UsageModel.DAILY_LIMIT_FREE
        if daily >= limit:
            remaining = 0
            if has_purchase:
                return False, "Has alcanzado el límite diario de Buddy Cloud. Intentá de nuevo mañana."
            else:
                return False, "Límite diario de prueba alcanzado (20 mensajes). Creá una cuenta con el email de tu compra para acceso ilimitado."
        remaining = limit - daily
        return True, f"{remaining} solicitudes restantes hoy"


class ConversationModel:
    @staticmethod
    def get_by_user(user_id: int) -> list[dict]:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["messages"] = __import__("json").loads(d["messages"])
            except (__import__("json").JSONDecodeError, TypeError):
                d["messages"] = []
            result.append(d)
        return result

    @staticmethod
    def create(user_id: int, title: str = "Nueva conversación", messages: str = "[]") -> Optional[dict]:
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO conversations (user_id, title, messages) VALUES (?, ?, ?)",
                (user_id, title, messages),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            conn.close()
            d = dict(row)
            try:
                d["messages"] = __import__("json").loads(d["messages"])
            except Exception:
                d["messages"] = []
            return d
        except Exception as e:
            conn.close()
            raise e

    @staticmethod
    def update(conv_id: int, user_id: int, title: str, messages: str) -> bool:
        conn = get_db()
        now = datetime.utcnow().isoformat()
        cur = conn.execute(
            "UPDATE conversations SET title = ?, messages = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (title, messages, now, conv_id, user_id),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0

    @staticmethod
    def delete(conv_id: int, user_id: int) -> bool:
        conn = get_db()
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
