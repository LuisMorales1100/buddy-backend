import os
import pathlib
import json
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models.async_database import get_async_db, get_session, AnimationPackModel
from routes.auth import decode_token

router = APIRouter(prefix="/animations", tags=["Animations"])

ANIMATIONS_DIR = pathlib.Path(__file__).parent.parent / "public" / "animations"

EXPRESSIONS = ["idle", "talking", "happy", "think", "surprised", "sad", "angry"]


async def _get_user_animation_features(session: AsyncSession, user_id):
    """Get all animation feature IDs the user owns via their products."""
    from models.async_database import product_get_user_products
    features = set()
    if not user_id:
        return features
    products = await product_get_user_products(session, user_id)
    for prod in products:
        anims = prod.get("features", {}).get("animations", [])
        features.update(anims)
    return features


def _build_gif_urls(pack, owned):
    gifs = {}
    if not owned or not pack.gifs:
        return gifs
    for expr in EXPRESSIONS:
        filename = pack.gifs.get(expr)
        if filename:
            file_path = ANIMATIONS_DIR / pack.id / filename
            if file_path.exists():
                gifs[expr] = f"/v1/animations/files/{pack.id}/{filename}"
    return gifs


async def _optional_user(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        payload = decode_token(authorization[7:])
        if payload:
            return payload
    return {"role": "anonymous", "user_id": None}


@router.get("/packs")
async def list_packs(
    user: dict = Depends(_optional_user),
    session: AsyncSession = Depends(get_session),
):
    """List all animation packs with ownership info and 7 GIF URLs."""
    async with get_async_db() as db_session:
        result = await db_session.execute(select(AnimationPackModel).order_by(AnimationPackModel.id))
        db_packs = result.scalars().all()

    user_id = user.get("user_id")
    user_features = await _get_user_animation_features(session, user_id) if user_id else set()

    result = []
    for pack in db_packs:
        pack_id = pack.id
        free = bool(pack.free)
        builtin = bool(pack.builtin)
        owned = free or builtin or pack_id in user_features
        result.append({
            "id": pack_id,
            "name": pack.name,
            "icon": pack.icon,
            "desc": pack.description,
            "builtin": builtin,
            "free": free,
            "owned": owned,
            "gifs": _build_gif_urls(pack, owned)
        })

    return {"packs": result}


@router.get("/packs/{pack_id}")
async def get_pack(
    pack_id: str,
    user: dict = Depends(_optional_user),
    session: AsyncSession = Depends(get_session),
):
    async with get_async_db() as db_session:
        result = await db_session.execute(
            select(AnimationPackModel).where(AnimationPackModel.id == pack_id)
        )
        pack = result.scalar_one_or_none()

    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    user_id = user.get("user_id")
    free = bool(pack.free)
    builtin = bool(pack.builtin)
    owned = free or builtin
    if not owned and user_id:
        user_features = await _get_user_animation_features(session, user_id)
        owned = pack_id in user_features

    return {
        "id": pack_id,
        "name": pack.name,
        "icon": pack.icon,
        "desc": pack.description,
        "builtin": builtin,
        "free": free,
        "owned": owned,
        "gifs": _build_gif_urls(pack, owned)
    }


@router.get("/files/{pack_id}/{filename}")
async def get_animation_file(pack_id: str, filename: str):
    # Security: prevent path traversal
    if ".." in pack_id or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid path")
    file_path = ANIMATIONS_DIR / pack_id / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), media_type="image/gif")
