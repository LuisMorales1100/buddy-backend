import os
import pathlib
import json
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from models.database import get_db, ProductModel
from routes.auth import decode_token

router = APIRouter(prefix="/animations", tags=["Animations"])

ANIMATIONS_DIR = pathlib.Path(__file__).parent.parent / "public" / "animations"


def _load_packs_from_db():
    """Load all animation packs from the database."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM animation_packs ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_user_animation_features(user_id):
    """Get all animation feature IDs the user owns via their products."""
    features = set()
    if not user_id:
        return features
    products = ProductModel.get_user_products(user_id)
    for prod in products:
        anims = prod.get("features", {}).get("animations", [])
        features.update(anims)
    return features


async def _optional_user(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        payload = decode_token(authorization[7:])
        if payload:
            return payload
    return {"role": "anonymous", "user_id": None}


@router.get("/packs")
async def list_packs(user: dict = Depends(_optional_user)):
    """List all animation packs with ownership info."""
    db_packs = _load_packs_from_db()
    user_id = user.get("user_id")
    user_features = _get_user_animation_features(user_id) if user_id else set()

    result = []
    for pack in db_packs:
        pack_id = pack["id"]
        has_file = pack["gif_file"] and (ANIMATIONS_DIR / pack["gif_file"]).exists()
        product_skus = json.loads(pack["product_skus"]) if isinstance(pack["product_skus"], str) else (pack["product_skus"] or [])
        free = bool(pack["free"])
        owned = free or pack_id in user_features
        result.append({
            "id": pack_id,
            "name": pack["name"],
            "icon": pack["icon"],
            "desc": pack["description"],
            "gifUrl": f"/v1/animations/files/{pack['gif_file']}" if (has_file and owned) else None,
            "gifWidth": pack["gif_width"],
            "gifHeight": pack["gif_height"],
            "builtin": bool(pack["builtin"]),
            "free": free,
            "owned": owned
        })

    return {"packs": result}


@router.get("/packs/{pack_id}")
async def get_pack(pack_id: str, user: dict = Depends(_optional_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM animation_packs WHERE id = ?", (pack_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Pack not found")
    pack = dict(row)

    has_file = pack["gif_file"] and (ANIMATIONS_DIR / pack["gif_file"]).exists()
    user_id = user.get("user_id")
    free = bool(pack["free"])
    owned = free
    if not owned and user_id:
        owned = pack_id in _get_user_animation_features(user_id)

    return {
        "id": pack_id,
        "name": pack["name"],
        "icon": pack["icon"],
        "desc": pack["description"],
        "gifUrl": f"/v1/animations/files/{pack['gif_file']}" if (has_file and owned) else None,
        "gifWidth": pack["gif_width"],
        "gifHeight": pack["gif_height"],
        "builtin": bool(pack["builtin"]),
        "free": free,
        "owned": owned
    }


@router.get("/files/{filename}")
async def get_animation_file(filename: str):
    file_path = ANIMATIONS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), media_type="image/gif")
