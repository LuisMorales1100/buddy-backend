import os
import json
from datetime import datetime
from contextlib import asynccontextmanager

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean, Float,
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
    serial_number = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False, default="Buddy")
    firmware_version = Column(String, nullable=False, default="4.0")
    mac_address = Column(String, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    paired_at = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    is_online = Column(Integer, nullable=False, default=0)
    ip_address = Column(String, nullable=True)
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
    messages = Column(Text, nullable=False, default="[]")
    device_id = Column(String, ForeignKey("devices.serial_number"), nullable=True)
    source = Column(String, nullable=False, default="text")
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
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


@asynccontextmanager
async def get_async_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
