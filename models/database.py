import os
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import CursorResult

# ── Connection ──────────────────────────────────────────────────
DB_URL = os.getenv("DATABASE_URL", "postgresql://buddy:buddy@localhost:5432/buddy_db")

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DB_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
    return _engine


def get_db():
    conn = _get_engine().connect()
    return conn


def close_db(conn):
    conn.close()


def _row_to_dict(row):
    """Convert a SQLAlchemy Row to dict (compat with old sqlite3.Row interface)."""
    if row is None:
        return None
    return dict(row._mapping)


def _rows_to_dicts(rows):
    return [dict(r._mapping) for r in rows]


# ── Model classes (PostgreSQL via SQLAlchemy) ──


class UserModel:
    @staticmethod
    def create(email: str, password_hash: str, name: str = "") -> Optional[int]:
        conn = get_db()
        try:
            result = conn.execute(
                text("INSERT INTO users (email, password_hash, name) VALUES (:email, :password_hash, :name) RETURNING id"),
                {"email": email, "password_hash": password_hash, "name": name},
            )
            row = result.fetchone()
            conn.commit()
            return row[0] if row else None
        except Exception:
            conn.rollback()
            return None
        finally:
            close_db(conn)

    @staticmethod
    def set_password(user_id: int, password_hash: str):
        conn = get_db()
        try:
            conn.execute(
                text("UPDATE users SET password_hash = :password_hash, password_set = 1 WHERE id = :id"),
                {"password_hash": password_hash, "id": user_id},
            )
            conn.commit()
        finally:
            close_db(conn)

    @staticmethod
    def get_by_email(email: str) -> Optional[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT * FROM users WHERE email = :email"),
                {"email": email},
            )
            return _row_to_dict(result.fetchone())
        finally:
            close_db(conn)

    @staticmethod
    def get_by_id(user_id: int) -> Optional[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT * FROM users WHERE id = :id"),
                {"id": user_id},
            )
            return _row_to_dict(result.fetchone())
        finally:
            close_db(conn)

    @staticmethod
    def set_shopify_id(user_id: int, shopify_id: str):
        conn = get_db()
        try:
            conn.execute(
                text("UPDATE users SET shopify_customer_id = :shopify_id WHERE id = :id"),
                {"shopify_id": shopify_id, "id": user_id},
            )
            conn.commit()
        finally:
            close_db(conn)


class DeviceModel:
    @staticmethod
    def register(serial: str, name: str, fw_version: str, mac: str) -> bool:
        conn = get_db()
        try:
            conn.execute(
                text("INSERT INTO devices (serial_number, name, firmware_version, mac_address) "
                     "VALUES (:serial, :name, :fw, :mac) ON CONFLICT (serial_number) DO NOTHING"),
                {"serial": serial, "name": name, "fw": fw_version, "mac": mac},
            )
            conn.commit()
            return True
        finally:
            close_db(conn)

    @staticmethod
    def get_by_serial(serial: str) -> Optional[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT * FROM devices WHERE serial_number = :serial"),
                {"serial": serial},
            )
            return _row_to_dict(result.fetchone())
        finally:
            close_db(conn)

    @staticmethod
    def pair(serial: str, user_id: int) -> bool:
        conn = get_db()
        try:
            conn.execute(
                text("UPDATE devices SET user_id = :user_id, paired_at = NOW() WHERE serial_number = :serial"),
                {"user_id": user_id, "serial": serial},
            )
            conn.commit()
            return True
        finally:
            close_db(conn)

    @staticmethod
    def get_user_devices(user_id: int) -> list[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT * FROM devices WHERE user_id = :user_id ORDER BY last_seen DESC"),
                {"user_id": user_id},
            )
            return _rows_to_dicts(result.fetchall())
        finally:
            close_db(conn)

    @staticmethod
    def update_last_seen(serial: str, ip: str, online: bool = True):
        conn = get_db()
        try:
            conn.execute(
                text("UPDATE devices SET last_seen = NOW(), ip_address = :ip, is_online = :online WHERE serial_number = :serial"),
                {"ip": ip, "online": int(online), "serial": serial},
            )
            conn.commit()
        finally:
            close_db(conn)

    @staticmethod
    def set_firmware_version(serial: str, version: str):
        conn = get_db()
        try:
            conn.execute(
                text("UPDATE devices SET firmware_version = :version WHERE serial_number = :serial"),
                {"version": version, "serial": serial},
            )
            conn.commit()
        finally:
            close_db(conn)


class PurchaseModel:
    @staticmethod
    def create(email: str, shopify_order_id: str, product_sku: str = "buddy_v1", product_name: str = "Buddy Assistant") -> bool:
        conn = get_db()
        try:
            conn.execute(
                text("INSERT INTO purchases (email, shopify_order_id, product_sku, product_name, order_created_at) "
                     "VALUES (:email, :order_id, :sku, :name, NOW()) ON CONFLICT (shopify_order_id) DO NOTHING"),
                {"email": email, "order_id": shopify_order_id, "sku": product_sku, "name": product_name},
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"[Purchase] Error: {e}")
            conn.rollback()
            return False
        finally:
            close_db(conn)

    @staticmethod
    def verify_by_order_id(order_id: str) -> bool:
        conn = get_db()
        try:
            result = conn.execute(
                text("UPDATE purchases SET verified = 1 WHERE shopify_order_id = :order_id"),
                {"order_id": order_id},
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            close_db(conn)

    @staticmethod
    def link_to_user(email: str, user_id: int):
        conn = get_db()
        try:
            conn.execute(
                text("UPDATE purchases SET user_id = :user_id, verified = 1 WHERE email = :email AND user_id IS NULL"),
                {"user_id": user_id, "email": email},
            )
            conn.commit()
        finally:
            close_db(conn)

    @staticmethod
    def user_has_purchase(user_id: int) -> bool:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT id FROM purchases WHERE user_id = :user_id AND verified = 1 LIMIT 1"),
                {"user_id": user_id},
            )
            return result.fetchone() is not None
        finally:
            close_db(conn)

    @staticmethod
    def get_by_email(email: str) -> Optional[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT * FROM purchases WHERE email = :email LIMIT 1"),
                {"email": email},
            )
            return _row_to_dict(result.fetchone())
        finally:
            close_db(conn)


class UsageModel:
    DAILY_LIMIT_FREE = 20
    DAILY_LIMIT_PAID = 999999

    @staticmethod
    def get_daily_count(user_id: int) -> int:
        conn = get_db()
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            result = conn.execute(
                text("SELECT request_count FROM llm_usage WHERE user_id = :user_id AND date = :date"),
                {"user_id": user_id, "date": today},
            )
            row = result.fetchone()
            return row[0] if row else 0
        finally:
            close_db(conn)

    @staticmethod
    def increment(user_id: int) -> int:
        conn = get_db()
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            conn.execute(
                text("INSERT INTO llm_usage (user_id, date, request_count) VALUES (:user_id, :date, 1) "
                     "ON CONFLICT (user_id, date) DO UPDATE SET request_count = llm_usage.request_count + 1"),
                {"user_id": user_id, "date": today},
            )
            conn.commit()
            result = conn.execute(
                text("SELECT request_count FROM llm_usage WHERE user_id = :user_id AND date = :date"),
                {"user_id": user_id, "date": today},
            )
            row = result.fetchone()
            return row[0] if row else 0
        finally:
            close_db(conn)

    @staticmethod
    def can_make_request(user_id: int) -> tuple[bool, str]:
        daily = UsageModel.get_daily_count(user_id)
        has_purchase = PurchaseModel.user_has_purchase(user_id)
        limit = UsageModel.DAILY_LIMIT_PAID if has_purchase else UsageModel.DAILY_LIMIT_FREE
        if daily >= limit:
            if has_purchase:
                return False, "Has alcanzado el límite diario de Buddy Cloud. Intentá de nuevo mañana."
            else:
                return False, "Límite diario de prueba alcanzado (20 mensajes). Creá una cuenta con el email de tu compra para acceso ilimitado."
        remaining = limit - daily
        return True, f"{remaining} solicitudes restantes hoy"


class ProductModel:
    @staticmethod
    def get_features(sku: str) -> Optional[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT features FROM products WHERE sku = :sku"),
                {"sku": sku},
            )
            row = result.fetchone()
            return json.loads(row[0]) if row else None
        finally:
            close_db(conn)

    @staticmethod
    def get_user_products(user_id: int) -> list[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT p.sku, p.name, p.features, pu.verified "
                     "FROM purchases pu "
                     "JOIN products p ON pu.product_sku = p.sku "
                     "WHERE pu.user_id = :user_id AND pu.verified = 1"),
                {"user_id": user_id},
            )
            rows = result.fetchall()
            result_list = []
            for r in rows:
                d = dict(r._mapping)
                d["features"] = json.loads(d["features"])
                result_list.append(d)
            return result_list
        finally:
            close_db(conn)


class FirmwareReleaseModel:
    @staticmethod
    def get_latest() -> Optional[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT * FROM firmware_releases ORDER BY id DESC LIMIT 1")
            )
            return _row_to_dict(result.fetchone())
        finally:
            close_db(conn)

    @staticmethod
    def list_all() -> list[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT version, changelog, file_size, critical, created_at FROM firmware_releases ORDER BY id DESC")
            )
            return _rows_to_dicts(result.fetchall())
        finally:
            close_db(conn)

    @staticmethod
    def create(version: str, changelog: str, file_path: str, file_size: int, critical: bool = False) -> bool:
        conn = get_db()
        try:
            conn.execute(
                text("INSERT INTO firmware_releases (version, changelog, file_path, file_size, critical) "
                     "VALUES (:version, :changelog, :file_path, :file_size, :critical)"),
                {"version": version, "changelog": changelog,
                 "file_path": file_path, "file_size": file_size, "critical": int(critical)},
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            close_db(conn)


class ConversationModel:
    @staticmethod
    def get_by_user(user_id: int, include_deleted: bool = False) -> list[dict]:
        conn = get_db()
        try:
            if include_deleted:
                result = conn.execute(
                    text("SELECT * FROM conversations WHERE user_id = :user_id ORDER BY updated_at DESC"),
                    {"user_id": user_id},
                )
            else:
                result = conn.execute(
                    text("SELECT * FROM conversations WHERE user_id = :user_id AND status = 'active' ORDER BY updated_at DESC"),
                    {"user_id": user_id},
                )
            rows = result.fetchall()
            result_list = []
            for r in rows:
                d = dict(r._mapping)
                try:
                    d["messages"] = json.loads(d["messages"])
                except (json.JSONDecodeError, TypeError):
                    d["messages"] = []
                result_list.append(d)
            return result_list
        finally:
            close_db(conn)

    @staticmethod
    def create(user_id: int, title: str = "Nueva conversación", messages: str = "[]") -> Optional[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("INSERT INTO conversations (user_id, title, messages) VALUES (:user_id, :title, :messages) RETURNING *"),
                {"user_id": user_id, "title": title, "messages": messages},
            )
            row = result.fetchone()
            conn.commit()
            if row:
                d = dict(row._mapping)
                try:
                    d["messages"] = json.loads(d["messages"])
                except Exception:
                    d["messages"] = []
                return d
            return None
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            close_db(conn)

    @staticmethod
    def update(conv_id: int, user_id: int, title: str, messages: str) -> bool:
        conn = get_db()
        try:
            result = conn.execute(
                text("UPDATE conversations SET title = :title, messages = :messages, updated_at = NOW() "
                     "WHERE id = :id AND user_id = :user_id"),
                {"title": title, "messages": messages, "id": conv_id, "user_id": user_id},
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            close_db(conn)

    @staticmethod
    def delete(conv_id: int, user_id: int) -> bool:
        conn = get_db()
        try:
            result = conn.execute(
                text("UPDATE conversations SET status = 'deleted', deleted_at = NOW() "
                     "WHERE id = :id AND user_id = :user_id AND status = 'active'"),
                {"id": conv_id, "user_id": user_id},
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            close_db(conn)

    @staticmethod
    def restore(conv_id: int, user_id: int) -> bool:
        conn = get_db()
        try:
            result = conn.execute(
                text("UPDATE conversations SET status = 'active', deleted_at = NULL "
                     "WHERE id = :id AND user_id = :user_id AND status = 'deleted'"),
                {"id": conv_id, "user_id": user_id},
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            close_db(conn)

    @staticmethod
    def create_sync(user_id: int, device_id: str, source: str, title: str = None) -> Optional[dict]:
        conn = get_db()
        try:
            if not title:
                title = "Nueva conversación"
            result = conn.execute(
                text("INSERT INTO conversations (user_id, device_id, source, title) "
                     "VALUES (:user_id, :device_id, :source, :title) RETURNING *"),
                {"user_id": user_id, "device_id": device_id, "source": source, "title": title},
            )
            row = result.fetchone()
            conn.commit()
            return dict(row._mapping) if row else None
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            close_db(conn)

    @staticmethod
    def get_sync_list(user_id: int, device_id: str = None, source: str = None,
                       include_deleted: bool = False,
                       limit: int = 50, offset: int = 0) -> list[dict]:
        conn = get_db()
        try:
            conditions = ["user_id = :user_id"]
            params = {"user_id": user_id, "limit": limit, "offset": offset}
            if device_id:
                conditions.append("device_id = :device_id")
                params["device_id"] = device_id
            if source:
                conditions.append("source = :source")
                params["source"] = source
            if not include_deleted:
                conditions.append("status = 'active'")
            where = " AND ".join(conditions)
            result = conn.execute(
                text(f"SELECT * FROM conversations WHERE {where} ORDER BY updated_at DESC LIMIT :limit OFFSET :offset"),
                params,
            )
            return _rows_to_dicts(result.fetchall())
        finally:
            close_db(conn)

    @staticmethod
    def get_sync_one(conv_id: int, user_id: int) -> Optional[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT * FROM conversations WHERE id = :id AND user_id = :user_id"),
                {"id": conv_id, "user_id": user_id},
            )
            row = result.fetchone()
            return dict(row._mapping) if row else None
        finally:
            close_db(conn)

    @staticmethod
    def delete_sync(conv_id: int, user_id: int) -> bool:
        conn = get_db()
        try:
            result = conn.execute(
                text("UPDATE conversations SET status = 'deleted', deleted_at = NOW() "
                     "WHERE id = :id AND user_id = :user_id AND status = 'active'"),
                {"id": conv_id, "user_id": user_id},
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            close_db(conn)

    @staticmethod
    def get_messages(conv_id: int) -> list[dict]:
        conn = get_db()
        try:
            result = conn.execute(
                text("SELECT * FROM conversation_messages WHERE conversation_id = :conv_id ORDER BY created_at"),
                {"conv_id": conv_id},
            )
            return _rows_to_dicts(result.fetchall())
        finally:
            close_db(conn)


class ConversationMessageModel:
    @staticmethod
    def create(conversation_id: int, role: str, content: str,
               audio_url: str = None, audio_duration_ms: int = None,
               expression: str = None, msg_id: str = None) -> Optional[dict]:
        conn = get_db()
        try:
            if not msg_id:
                import uuid
                msg_id = str(uuid.uuid4())
            result = conn.execute(
                text("INSERT INTO conversation_messages (id, conversation_id, role, content, audio_url, audio_duration_ms, expression) "
                     "VALUES (:id, :conv_id, :role, :content, :audio_url, :audio_duration_ms, :expression) RETURNING *"),
                {"id": msg_id, "conv_id": conversation_id, "role": role, "content": content,
                 "audio_url": audio_url, "audio_duration_ms": audio_duration_ms, "expression": expression},
            )
            row = result.fetchone()
            conn.commit()
            return dict(row._mapping) if row else None
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            close_db(conn)
