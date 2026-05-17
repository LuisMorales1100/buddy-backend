from fastapi import APIRouter, HTTPException
from models.schemas import FacePack

router = APIRouter(prefix="/faces", tags=["Faces"])

# En producción: cargar de DB o S3
PACKS = {
    "default": FacePack(
        version="1.0.0",
        emotions={
            "neutral": {"le":100, "re":100, "mc":0, "lb":0, "rb":0, "bh":0, "bl":0, "sp":0, "hp":0},
            "happy": {"le":96, "re":96, "mc":90, "lb":-4, "rb":-4, "bh":-5, "bl":82, "sp":90, "hp":40},
            "sad": {"le":58, "re":58, "mc":-75, "lb":-15, "rb":15, "bh":3, "bl":0, "sp":0, "hp":0},
            "surprised": {"le":100, "re":100, "mc":50, "lb":0, "rb":0, "bh":-10, "bl":20, "sp":60, "hp":0},
            "angry": {"le":75, "re":75, "mc":-45, "lb":25, "rb":-25, "bh":5, "bl":50, "sp":0, "hp":0},
            "love": {"le":95, "re":95, "mc":80, "lb":3, "rb":3, "bh":-3, "bl":90, "sp":100, "hp":100},
            "sleepy": {"le":32, "re":32, "mc":-5, "lb":-5, "rb":5, "bh":8, "bl":30, "sp":0, "hp":0},
            "wink": {"le":85, "re":12, "mc":70, "lb":8, "rb":-8, "bh":-2, "bl":60, "sp":75, "hp":0},
            "excited": {"le":100, "re":100, "mc":95, "lb":8, "rb":8, "bh":-8, "bl":85, "sp":100, "hp":80},
        }
    ),
    "kawaii_v2": FacePack(
        version="2.0.0",
        emotions={
            "neutral": {"le":100, "re":100, "mc":0, "lb":0, "rb":0, "bh":0, "bl":0, "sp":0, "hp":0},
            "happy": {"le":100, "re":100, "mc":100, "lb":0, "rb":0, "bh":-8, "bl":90, "sp":100, "hp":50},
            "uwu": {"le":85, "re":85, "mc":60, "lb":12, "rb":12, "bh":-4, "bl":70, "sp":80, "hp":30},
            "owo": {"le":90, "re":90, "mc":75, "lb":5, "rb":5, "bh":-6, "bl":75, "sp":85, "hp":40},
        }
    )
}

@router.get("/{pack_id}", response_model=FacePack)
async def get_face_pack(pack_id: str):
    pack = PACKS.get(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    return pack