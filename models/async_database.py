import os
import json
from datetime import datetime
from contextlib import asynccontextmanager

from sqlalchemy import (
    create_engine, select, Column, Integer, String, Text, Boolean, Float,
    DateTime, ForeignKey, UniqueConstraint, JSON, Index,
    event as sa_event,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# ── PostgreSQL connection ───────────────────────────────────────
SYNC_DB_URL = os.getenv("DATABASE_URL", "postgresql://buddy:buddy@localhost:5432/buddy_db")

# Async URL — replace scheme for asyncpg
ASYNC_DB_URL = SYNC_DB_URL.replace("postgresql://", "postgresql+asyncpg://")

async_engine = create_async_engine(
    ASYNC_DB_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    shopify_customer_id = Column(String, nullable=True)
    stripe_customer_id = Column(String, nullable=True)
    password_set = Column(Integer, nullable=False, default=0)


class DeviceModel(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    serial = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)
    last_known_ip = Column(String, nullable=True)
    config = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    user = relationship("UserModel", back_populates="devices")


UserModel.devices = relationship("DeviceModel", back_populates="user")


class PurchaseModel(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    email = Column(String, nullable=False)
    shopify_order_id = Column(String, unique=True, nullable=True)
    product_sku = Column(String, nullable=False, default="buddy_v1")
    product_name = Column(String, nullable=False, default="Buddy Assistant")
    order_created_at = Column(DateTime, nullable=True)
    verified = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class LLMUsageModel(Base):
    __tablename__ = "llm_usage"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(String, nullable=False)
    request_count = Column(Integer, nullable=False, default=0)
    __table_args__ = (UniqueConstraint("user_id", "date"),)


class ConversationModel(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False, default="Nueva conversación")
    origin_device_serial = Column(String, nullable=False, default='app_general')
    linked_device_serials = Column(JSON, nullable=False, default=list)
    source = Column(String, nullable=False, default="text")
    status = Column(String, nullable=False, default="active")
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    messages_rel = relationship("ConversationMessageModel", back_populates="conversation",
                                 cascade="all, delete-orphan", order_by="ConversationMessageModel.created_at")


class ConversationMessageModel(Base):
    __tablename__ = "conversation_messages"
    id = Column(String, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    audio_url = Column(String, nullable=True)
    audio_duration_ms = Column(Integer, nullable=True)
    expression = Column(String, nullable=True)
    device_serial = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    conversation = relationship("ConversationModel", back_populates="messages_rel")


class ProductModel(Base):
    __tablename__ = "products"
    sku = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    features = Column(Text, nullable=False, default="{}")


class AnimationPackModel(Base):
    __tablename__ = "animation_packs"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    icon = Column(String, nullable=False, default="")
    description = Column(String, nullable=False, default="")
    builtin = Column(Integer, nullable=False, default=0)
    free = Column(Integer, nullable=False, default=0)
    product_skus = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    gifs = Column(JSON, nullable=False, default=dict)


class FirmwareReleaseModel(Base):
    __tablename__ = "firmware_releases"
    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String, unique=True, nullable=False)
    changelog = Column(Text, nullable=True)
    file_path = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False, default=0)
    critical = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    @staticmethod
    async def get_latest(session: AsyncSession):
        result = await session.execute(
            select(FirmwareReleaseModel).order_by(FirmwareReleaseModel.id.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession):
        result = await session.execute(
            select(FirmwareReleaseModel).order_by(FirmwareReleaseModel.id.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def create(session: AsyncSession, version: str, changelog: str, file_path: str, file_size: int, critical: bool = False):
        release = FirmwareReleaseModel(
            version=version, changelog=changelog,
            file_path=file_path, file_size=file_size,
            critical=1 if critical else 0,
        )
        session.add(release)
        await session.flush()
        return release


# ── Async query helpers (replacement for old database.py static methods) ──

def _orm_to_dict(instance) -> dict:
    """Convert SQLAlchemy ORM instance to dict with column names."""
    if instance is None:
        return None
    return {c.name: getattr(instance, c.name) for c in instance.__table__.columns}


async def user_create(session: AsyncSession, email: str, password_hash: str, name: str = ""):
    user = UserModel(email=email, password_hash=password_hash, name=name)
    session.add(user)
    await session.flush()
    return user.id


async def user_get_by_email(session: AsyncSession, email: str):
    result = await session.execute(select(UserModel).where(UserModel.email == email))
    return _orm_to_dict(result.scalar_one_or_none())


async def user_get_by_id(session: AsyncSession, user_id: int):
    result = await session.execute(select(UserModel).where(UserModel.id == user_id))
    return _orm_to_dict(result.scalar_one_or_none())


async def user_set_password(session: AsyncSession, user_id: int, password_hash: str):
    result = await session.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.password_hash = password_hash
        user.password_set = 1


async def user_set_shopify_id(session: AsyncSession, user_id: int, shopify_id: str):
    result = await session.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.shopify_customer_id = shopify_id


async def device_get_by_serial(session: AsyncSession, serial: str):
    result = await session.execute(select(DeviceModel).where(DeviceModel.serial == serial))
    return result.scalar_one_or_none()


async def device_get_user_devices(session: AsyncSession, user_id: int):
    result = await session.execute(
        select(DeviceModel).where(DeviceModel.user_id == user_id).order_by(DeviceModel.created_at.desc())
    )
    devices = []
    for d in result.scalars().all():
        row = _orm_to_dict(d)
        if isinstance(row.get("config"), str):
            row["config"] = json.loads(row["config"])
        elif row.get("config") is None:
            row["config"] = {}
        devices.append(row)
    return devices


async def device_upsert(session: AsyncSession, serial: str, user_id: int | None = None, name: str | None = None, last_known_ip: str | None = None, config: dict | None = None):
    existing = await session.execute(
        select(DeviceModel).where(DeviceModel.serial == serial)
    )
    device = existing.scalar_one_or_none()
    if device:
        if name is not None:
            device.name = name
        if last_known_ip is not None:
            device.last_known_ip = last_known_ip
        if user_id is not None:
            device.user_id = user_id
        if config is not None:
            current = dict(device.config or {})
            current.update(config)
            device.config = current
    else:
        if last_known_ip and user_id:
            by_ip = await session.execute(
                select(DeviceModel).where(
                    DeviceModel.last_known_ip == last_known_ip,
                    DeviceModel.user_id == user_id
                )
            )
            device = by_ip.scalar_one_or_none()
        if device:
            device.serial = serial
            if name is not None:
                device.name = name
            if last_known_ip is not None:
                device.last_known_ip = last_known_ip
            if config is not None:
                current = dict(device.config or {})
                current.update(config)
                device.config = current
        else:
            device = DeviceModel(
                serial=serial,
                user_id=user_id,
                name=name or 'Buddy',
                last_known_ip=last_known_ip or '',
                config=config or {},
            )
            session.add(device)
    await session.flush()
    return _orm_to_dict(device)


async def purchase_create(session: AsyncSession, email: str, shopify_order_id: str,
                          product_sku: str = "buddy_v1", product_name: str = "Buddy Assistant"):
    existing = await session.execute(
        select(PurchaseModel).where(PurchaseModel.shopify_order_id == shopify_order_id)
    )
    if existing.scalar_one_or_none():
        return True
    purchase = PurchaseModel(
        email=email, shopify_order_id=shopify_order_id,
        product_sku=product_sku, product_name=product_name,
        order_created_at=datetime.utcnow(),
    )
    session.add(purchase)
    await session.flush()
    return True


async def purchase_verify_by_order_id(session: AsyncSession, order_id: str):
    result = await session.execute(
        select(PurchaseModel).where(PurchaseModel.shopify_order_id == order_id)
    )
    purchase = result.scalar_one_or_none()
    if purchase:
        purchase.verified = 1
        return True
    return False


async def purchase_link_to_user(session: AsyncSession, email: str, user_id: int):
    result = await session.execute(
        select(PurchaseModel).where(PurchaseModel.email == email, PurchaseModel.user_id.is_(None))
    )
    for p in result.scalars().all():
        p.user_id = user_id
        p.verified = 1


async def purchase_user_has_purchase(session: AsyncSession, user_id: int):
    result = await session.execute(
        select(PurchaseModel).where(
            PurchaseModel.user_id == user_id,
            PurchaseModel.verified == 1,
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def purchase_get_by_email(session: AsyncSession, email: str):
    result = await session.execute(
        select(PurchaseModel).where(PurchaseModel.email == email).limit(1)
    )
    return _orm_to_dict(result.scalar_one_or_none())


DAILY_LIMIT_FREE = 20
DAILY_LIMIT_PAID = 999999


async def usage_get_daily_count(session: AsyncSession, user_id: int):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    result = await session.execute(
        select(LLMUsageModel).where(
            LLMUsageModel.user_id == user_id,
            LLMUsageModel.date == today,
        )
    )
    usage = result.scalar_one_or_none()
    return usage.request_count if usage else 0


async def usage_increment(session: AsyncSession, user_id: int):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    result = await session.execute(
        select(LLMUsageModel).where(
            LLMUsageModel.user_id == user_id,
            LLMUsageModel.date == today,
        )
    )
    usage = result.scalar_one_or_none()
    if usage:
        usage.request_count += 1
    else:
        usage = LLMUsageModel(user_id=user_id, date=today, request_count=1)
        session.add(usage)
    await session.flush()
    return usage.request_count


async def usage_can_make_request(session: AsyncSession, user_id: int):
    daily = await usage_get_daily_count(session, user_id)
    has_purchase = await purchase_user_has_purchase(session, user_id)
    limit = DAILY_LIMIT_PAID if has_purchase else DAILY_LIMIT_FREE
    if daily >= limit:
        if has_purchase:
            return False, "Has alcanzado el límite diario de Buddy Cloud. Intentá de nuevo mañana."
        else:
            return False, "Límite diario de prueba alcanzado (20 mensajes). Creá una cuenta con el email de tu compra para acceso ilimitado."
    remaining = limit - daily
    return True, f"{remaining} solicitudes restantes hoy"


async def product_get_features(session: AsyncSession, sku: str):
    result = await session.execute(select(ProductModel).where(ProductModel.sku == sku))
    product = result.scalar_one_or_none()
    if not product:
        return None
    return json.loads(product.features) if isinstance(product.features, str) else product.features


async def product_get_user_products(session: AsyncSession, user_id: int):
    from sqlalchemy import join
    j = join(PurchaseModel, ProductModel, PurchaseModel.product_sku == ProductModel.sku)
    result = await session.execute(
        select(ProductModel.sku, ProductModel.name, ProductModel.features, PurchaseModel.verified).select_from(j).where(
            PurchaseModel.user_id == user_id,
            PurchaseModel.verified == 1,
        )
    )
    products = []
    for row in result.all():
        features = json.loads(row.features) if isinstance(row.features, str) else row.features
        products.append({"sku": row.sku, "name": row.name, "features": features})
    return products


@asynccontextmanager
async def get_async_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session():
    """FastAPI dependency: yields an AsyncSession for request handlers."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
