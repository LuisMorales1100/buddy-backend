from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, cast
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from models.async_database import ConversationModel, ConversationMessageModel, get_session
from routes.auth import get_current_user
from pydantic import BaseModel
from typing import Optional, List, Literal
import uuid
import hashlib
from datetime import datetime

router = APIRouter(prefix="/conversations", tags=["conversations"])

# ============ SCHEMAS ============

class MessagePayload(BaseModel):
    id: Optional[str] = None
    role: str = 'user'
    content: str = ''
    audio_url: Optional[str] = None
    audio_duration_ms: Optional[int] = None
    expression: Optional[str] = None
    device_serial: Optional[str] = None
    agent_id: Optional[str] = None

class ConversationSyncCreate(BaseModel):
    localId: Optional[str] = None
    origin_device_serial: Optional[str] = None
    title: Optional[str] = 'Sin título'
    source: str = 'text'
    messages: List[MessagePayload] = []

    model_config = {"extra": "allow"}

class ConversationSyncUpdate(BaseModel):
    title: Optional[str] = None
    messages: List[MessagePayload] = []
    link_device_serial: Optional[str] = None

class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    device_serial: Optional[str] = None
    agent_id: Optional[str] = None
    created_at: datetime

class DeviceLinkAction(BaseModel):
    action: Literal['add', 'remove', 'change_origin']
    serial: str

class ConversationOut(BaseModel):
    id: int
    origin_device_serial: Optional[str] = None
    linked_device_serials: list = []
    title: Optional[str]
    source: str
    status: str
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    archived: bool = False
    archived_at: Optional[datetime] = None
    messages: List[MessageOut] = []

class ConversationListItem(BaseModel):
    id: int
    origin_device_serial: Optional[str] = None
    linked_device_serials: list = []
    title: Optional[str]
    source: str
    status: str
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    archived: bool = False
    archived_at: Optional[datetime] = None
    messages_count: int = 0

# ============ ENDPOINTS ============

@router.post("/sync", response_model=ConversationOut)
async def sync_create_conversation(
    data: ConversationSyncCreate,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Crear nueva conversación desde sincronización offline"""
    print(f"\n[POST /conversations/sync] Creando: {data.title}")

    origin_device_serial = data.origin_device_serial
    if not origin_device_serial or origin_device_serial.strip() == '':
        raise HTTPException(status_code=400, detail="origin_device_serial is required")

    conversation = ConversationModel(
        origin_device_serial=origin_device_serial,
        linked_device_serials=[],
        user_id=user["user_id"],
        title=data.title or 'Sin título',
        source=data.source,
        status='active',
        archived=False,
    )
    session.add(conversation)
    await session.flush()

    for msg_data in data.messages:
        msg_id = msg_data.id or hashlib.sha256(
            f"{conversation.id}:{msg_data.role}:{msg_data.content}".encode()
        ).hexdigest()[:32]
        stmt = pg_insert(ConversationMessageModel).values(
            id=msg_id,
            conversation_id=conversation.id,
            role=msg_data.role,
            content=msg_data.content,
            audio_url=msg_data.audio_url,
            audio_duration_ms=msg_data.audio_duration_ms,
            expression=msg_data.expression,
            device_serial=msg_data.device_serial,
            agent_id=msg_data.agent_id,
        ).on_conflict_do_nothing(index_elements=['id'])
        await session.execute(stmt)

    await session.commit()
    await session.refresh(conversation)

    print(f"[POST /conversations/sync] ✅ Creada id={conversation.id}")
    return await _conv_to_response(conversation, session)


@router.put("/sync/{conversation_id}", response_model=ConversationOut)
async def sync_update_conversation(
    conversation_id: int,
    data: ConversationSyncUpdate,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Actualizar conversación desde sincronización"""
    print(f"\n[PUT /conversations/sync/{conversation_id}]")

    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == user["user_id"],
            ConversationModel.archived == False,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if data.title:
        conversation.title = data.title

    if data.link_device_serial:
        current = list(conversation.linked_device_serials or [])
        if data.link_device_serial not in current:
            current.append(data.link_device_serial)
            conversation.linked_device_serials = current

    for msg_data in data.messages:
        msg_id = msg_data.id or hashlib.sha256(
            f"{conversation_id}:{msg_data.role}:{msg_data.content}".encode()
        ).hexdigest()[:32]
        stmt = pg_insert(ConversationMessageModel).values(
            id=msg_id,
            conversation_id=conversation_id,
            role=msg_data.role,
            content=msg_data.content,
            audio_url=msg_data.audio_url,
            audio_duration_ms=msg_data.audio_duration_ms,
            expression=msg_data.expression,
            device_serial=msg_data.device_serial,
            agent_id=msg_data.agent_id,
        ).on_conflict_do_nothing(index_elements=['id'])
        await session.execute(stmt)

    conversation.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(conversation)

    print(f"[PUT /conversations/sync/{conversation_id}] ✅")
    return await _conv_to_response(conversation, session)


@router.get("/sync", response_model=List[ConversationListItem])
async def list_sync_conversations(
    device_serial: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    include_deleted: bool = Query(False),
    limit: int = Query(50),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Listar conversaciones sincronizadas (nunca archivadas)"""
    print(f"\n[GET /conversations/sync] user_id={user['user_id']}")

    stmt = select(ConversationModel).where(
        ConversationModel.user_id == user["user_id"],
        ConversationModel.archived == False,
    )
    if device_serial:
        if device_serial == 'all':
            pass
        elif device_serial == 'app_general':
            stmt = stmt.where(ConversationModel.origin_device_serial == 'app_general')
        else:
            stmt = stmt.where(
                (ConversationModel.origin_device_serial == device_serial)
                | cast(ConversationModel.linked_device_serials, JSONB).contains([device_serial])
            )
    if source:
        stmt = stmt.where(ConversationModel.source == source)
    if not include_deleted:
        stmt = stmt.where(ConversationModel.status == 'active')

    stmt = stmt.order_by(ConversationModel.updated_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    conversations = result.scalars().all()

    print(f"[GET /conversations/sync] → {len(conversations)} convs")
    return [
        ConversationListItem(
            id=c.id,
            origin_device_serial=c.origin_device_serial,
            linked_device_serials=list(c.linked_device_serials or []),
            title=c.title,
            source=c.source,
            status=c.status,
            created_at=c.created_at,
            updated_at=c.updated_at,
            deleted_at=c.deleted_at,
            archived=c.archived,
            archived_at=c.archived_at,
            messages_count=0,
        )
        for c in conversations
    ]


@router.get("/sync/{conversation_id}", response_model=ConversationOut)
async def get_sync_conversation(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Obtener conversación sync con mensajes"""
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == user["user_id"],
            ConversationModel.archived == False,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await _conv_to_response(conversation, session)


@router.delete("/sync/{conversation_id}")
async def delete_sync_conversation(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Soft delete"""
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == user["user_id"],
            ConversationModel.archived == False,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.status = 'deleted'
    conversation.deleted_at = datetime.utcnow()
    await session.commit()
    return {"status": "deleted", "conversation_id": conversation_id}


@router.post("/sync/{conversation_id}/restore")
async def restore_sync_conversation(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Restaurar conversación eliminada"""
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == user["user_id"],
            ConversationModel.status == 'deleted',
            ConversationModel.archived == False,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Deleted conversation not found")

    conversation.status = 'active'
    conversation.deleted_at = None
    await session.commit()
    return {"status": "restored", "conversation_id": conversation_id}


@router.post("/sync/{conversation_id}/permanent-delete")
async def permanent_delete_sync_conversation(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Archivar (no se muestra más, datos conservados)"""
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == user["user_id"],
            ConversationModel.archived == False,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.archived = True
    conversation.archived_at = datetime.utcnow()
    await session.commit()
    return {"status": "permanently_deleted", "conversation_id": conversation_id}


# ── Legacy conversation routes (pre-sync) ──

@router.get("")
async def list_conversations(
    include_deleted: bool = Query(False),
    device_serial: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Listar conversaciones legacy"""
    stmt = select(ConversationModel).where(
        ConversationModel.user_id == user["user_id"],
        ConversationModel.archived == False,
    )
    if device_serial:
        if device_serial == 'all':
            pass
        elif device_serial == 'app_general':
            stmt = stmt.where(ConversationModel.origin_device_serial == 'app_general')
        else:
            stmt = stmt.where(
                or_(
                    ConversationModel.origin_device_serial == device_serial,
                    cast(ConversationModel.linked_device_serials, JSONB).contains([device_serial])
                )
            )
    if not include_deleted:
        stmt = stmt.where(ConversationModel.status == 'active')
    stmt = stmt.order_by(ConversationModel.updated_at.desc())
    result = await session.execute(stmt)
    convs = result.scalars().all()

    responses = []
    for c in convs:
        msg_result = await session.execute(
            select(ConversationMessageModel).where(
                ConversationMessageModel.conversation_id == c.id
            ).order_by(ConversationMessageModel.created_at)
        )
        msgs = msg_result.scalars().all()
        responses.append({
            "id": c.id,
            "origin_device_serial": c.origin_device_serial,
            "linked_device_serials": list(c.linked_device_serials or []),
            "title": c.title,
            "source": c.source,
            "status": c.status,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "deleted_at": c.deleted_at.isoformat() if c.deleted_at else None,
            "archived": c.archived,
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "device_serial": m.device_serial,
                    "agent_id": m.agent_id,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in msgs
            ],
        })
    return {"conversations": responses}


@router.post("")
async def create_conversation(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Crear conversación legacy"""
    body = await request.json()
    title = body.get("title", "Nueva conversación")
    messages_data = body.get("messages", [])

    if len(messages_data) > 50:
        raise HTTPException(status_code=400, detail="Máximo 50 mensajes")

    origin_device_serial = body.get("origin_device_serial", "")
    if not origin_device_serial or origin_device_serial.strip() == '':
        raise HTTPException(status_code=400, detail="origin_device_serial is required")

    conv = ConversationModel(
        origin_device_serial=origin_device_serial,
        linked_device_serials=[],
        user_id=user["user_id"],
        title=title,
        source="text",
        status="active",
        archived=False,
    )
    session.add(conv)
    await session.flush()

    for m in messages_data:
        msg = ConversationMessageModel(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            role=m.get("role", "user"),
            content=m.get("content", ""),
            agent_id=m.get("agent_id"),
        )
        session.add(msg)

    await session.commit()
    await session.refresh(conv)

    msg_result = await session.execute(
        select(ConversationMessageModel).where(
            ConversationMessageModel.conversation_id == conv.id
        ).order_by(ConversationMessageModel.created_at)
    )
    msgs = msg_result.scalars().all()
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "agent_id": m.agent_id, "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in msgs
        ],
    }


@router.put("/{conv_id}")
async def update_conversation(
    conv_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Actualizar conversación legacy"""
    body = await request.json()
    title = body.get("title")
    messages_data = body.get("messages")

    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conv_id,
            ConversationModel.user_id == user["user_id"],
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if title:
        conv.title = title

    if messages_data is not None:
        if len(messages_data) > 50:
            raise HTTPException(status_code=400, detail="Máximo 50 mensajes")
        # Replace messages: delete old, insert new
        old_msgs = await session.execute(
            select(ConversationMessageModel).where(
                ConversationMessageModel.conversation_id == conv_id
            )
        )
        for m in old_msgs.scalars().all():
            await session.delete(m)
        for m in messages_data:
            msg = ConversationMessageModel(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role=m.get("role", "user"),
                content=m.get("content", ""),
                agent_id=m.get("agent_id"),
            )
            session.add(msg)

    conv.updated_at = datetime.utcnow()
    await session.commit()
    return {"status": "updated", "id": conv_id}


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Soft delete legacy"""
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conv_id,
            ConversationModel.user_id == user["user_id"],
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv.status = 'deleted'
    conv.deleted_at = datetime.utcnow()
    await session.commit()
    return {"status": "deleted", "id": conv_id}


@router.post("/{conv_id}/restore")
async def restore_conversation(
    conv_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Restaurar legacy"""
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conv_id,
            ConversationModel.user_id == user["user_id"],
            ConversationModel.status == 'deleted',
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Deleted conversation not found")

    conv.status = 'active'
    conv.deleted_at = None
    await session.commit()
    return {"status": "restored", "conversation_id": conv_id}


@router.post("/{conv_id}/permanent-delete")
async def permanent_delete_conversation(
    conv_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Archivar legacy"""
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conv_id,
            ConversationModel.user_id == user["user_id"],
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv.archived = True
    conv.archived_at = datetime.utcnow()
    await session.commit()
    return {"status": "archived", "conversation_id": conv_id}


@router.put("/{conv_id}/devices", response_model=ConversationOut)
async def manage_conversation_devices(
    conv_id: int,
    data: DeviceLinkAction,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Add, remove, or change origin device for a conversation"""
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.id == conv_id,
            ConversationModel.user_id == user["user_id"],
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    current_serials = list(conv.linked_device_serials or [])

    if data.action == 'add':
        if data.serial not in current_serials:
            current_serials.append(data.serial)
        conv.linked_device_serials = current_serials
    elif data.action == 'remove':
        if data.serial in current_serials:
            current_serials.remove(data.serial)
        conv.linked_device_serials = current_serials
    elif data.action == 'change_origin':
        if not data.serial.strip():
            raise HTTPException(status_code=400, detail="Serial is required for change_origin")
        conv.origin_device_serial = data.serial

    conv.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(conv)
    return await _conv_to_response(conv, session)


# ============ HELPERS ============

async def _conv_to_response(conversation: ConversationModel, session: AsyncSession) -> ConversationOut:
    msg_result = await session.execute(
        select(ConversationMessageModel).where(
            ConversationMessageModel.conversation_id == conversation.id
        ).order_by(ConversationMessageModel.created_at)
    )
    messages = msg_result.scalars().all()
    return ConversationOut(
        id=conversation.id,
        origin_device_serial=conversation.origin_device_serial,
        linked_device_serials=list(conversation.linked_device_serials or []),
        title=conversation.title,
        source=conversation.source,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        deleted_at=conversation.deleted_at,
        archived=conversation.archived,
        archived_at=conversation.archived_at,
        messages=[
            MessageOut(id=m.id, role=m.role, content=m.content, device_serial=m.device_serial, agent_id=m.agent_id, created_at=m.created_at)
            for m in messages
        ],
    )
