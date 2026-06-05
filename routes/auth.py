import os
import hashlib
import secrets
import bcrypt
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import Optional

JWT_SECRET = os.getenv("JWT_SECRET", "buddy_dev_secret_change_in_production")
REFRESH_SECRET = os.getenv("REFRESH_SECRET", "buddy_refresh_dev_secret_change_in_production")
TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30

router = APIRouter(prefix="", tags=["Auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class SetPasswordRequest(BaseModel):
    email: str
    password: str
    code: str


class PairDeviceRequest(BaseModel):
    serial: str
    name: str = "Buddy"


class DeviceRegisterRequest(BaseModel):
    serial: str
    name: str = "Buddy"
    firmware_version: str = "4.0"
    mac: str = ""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        try:
            return bcrypt.checkpw(password.encode(), stored.encode())
        except Exception:
            return False
    parts = stored.split(":")
    if len(parts) != 2:
        return False
    salt, expected = parts
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return h == expected


def _b64encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign(payload: dict, secret: str) -> str:
    header_b64 = _b64encode(b'{"alg":"HS256","typ":"JWT"}')
    payload_b64 = _b64encode(
        __import__("json").dumps(payload, separators=(",", ":")).encode()
    )
    sig_input = f"{header_b64}.{payload_b64}"
    sig = hashlib.sha256((sig_input + secret).encode()).hexdigest()
    sig_b64 = _b64encode(sig.encode())
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _verify(token: str, secret: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        import json
        payload = json.loads(_b64decode(parts[1]))
        sig_input = f"{parts[0]}.{parts[1]}"
        expected_sig = hashlib.sha256((sig_input + secret).encode()).hexdigest()
        sig_b64 = _b64encode(expected_sig.encode())
        if sig_b64 != parts[2]:
            return None
        if payload.get("exp", 0) < datetime.utcnow().timestamp():
            return None
        return payload
    except Exception:
        return None


def create_access_token(payload: dict) -> str:
    exp = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    payload["exp"] = int(exp.timestamp())
    payload["iat"] = int(datetime.utcnow().timestamp())
    payload["type"] = "access"
    return _sign(payload, JWT_SECRET)


def create_refresh_token(payload: dict) -> str:
    exp = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload["exp"] = int(exp.timestamp())
    payload["iat"] = int(datetime.utcnow().timestamp())
    payload["type"] = "refresh"
    return _sign(payload, REFRESH_SECRET)


def decode_token(token: str) -> Optional[dict]:
    return _verify(token, JWT_SECRET)


async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


# ===== Internal registration (webhook-only) =====
def internal_register(email: str, name: str = "") -> Optional[int]:
    from models.database import UserModel
    return UserModel.create(email, "", name)


# ===== Routes =====

@router.post("/auth/login")
async def login(req: LoginRequest):
    from models.database import UserModel, PurchaseModel, ProductModel

    user = UserModel.get_by_email(req.email)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    if not user.get("password_set", 0):
        raise HTTPException(
            status_code=401,
            detail="Debés configurar tu contraseña primero. Revisá tu email.",
        )

    PurchaseModel.link_to_user(req.email, user["id"])
    has_purchase = PurchaseModel.user_has_purchase(user["id"])

    if not has_purchase:
        raise HTTPException(
            status_code=402,
            detail="Necesitás una compra verificada de Buddy para acceder.",
        )

    products = ProductModel.get_user_products(user["id"])

    payload = {"user_id": user["id"], "email": user["email"], "role": "user"}
    return {
        "token": create_access_token(payload),
        "refresh_token": create_refresh_token(payload),
        "user_id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "has_purchase": True,
        "products": products,
    }


@router.post("/auth/set-password")
async def set_password(req: SetPasswordRequest):
    from models.database import UserModel

    expected = hashlib.sha256((req.email + JWT_SECRET).encode()).hexdigest()[:12]
    if req.code != expected:
        raise HTTPException(status_code=401, detail="Código inválido o expirado")

    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    user = UserModel.get_by_email(req.email)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    UserModel.set_password(user["id"], hash_password(req.password))

    return {"status": "ok", "message": "Contraseña configurada. Ya podés iniciar sesión."}


@router.post("/auth/refresh")
async def refresh(req: RefreshRequest):
    payload = _verify(req.refresh_token, REFRESH_SECRET)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    fresh_payload = {
        "user_id": payload["user_id"],
        "email": payload["email"],
        "role": payload.get("role", "user"),
    }
    return {
        "token": create_access_token(fresh_payload),
        "refresh_token": create_refresh_token(fresh_payload),
    }


@router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    from models.database import UserModel, DeviceModel, PurchaseModel, ProductModel

    db_user = UserModel.get_by_id(user["user_id"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    devices = DeviceModel.get_user_devices(user["user_id"])
    has_purchase = PurchaseModel.user_has_purchase(user["user_id"])
    products = ProductModel.get_user_products(user["user_id"]) if has_purchase else []

    return {
        "user_id": db_user["id"],
        "email": db_user["email"],
        "name": db_user["name"],
        "shopify_customer_id": db_user.get("shopify_customer_id"),
        "has_purchase": has_purchase,
        "products": products,
        "devices": devices,
    }


@router.post("/devices/register")
async def register_device(req: DeviceRegisterRequest):
    from models.database import DeviceModel

    DeviceModel.register(req.serial, req.name, req.firmware_version, req.mac)
    DeviceModel.update_last_seen(req.serial, "", online=True)

    token = create_access_token(
        {"serial": req.serial, "role": "device"}
    )
    return {
        "status": "registered",
        "serial": req.serial,
        "token": token,
    }


@router.post("/devices/pair")
async def pair_device(
    req: PairDeviceRequest, user: dict = Depends(get_current_user)
):
    from models.database import DeviceModel

    DeviceModel.pair(req.serial, user["user_id"])
    return {"status": "paired", "serial": req.serial, "user_id": user["user_id"]}


@router.get("/devices")
async def list_devices(user: dict = Depends(get_current_user)):
    from models.database import DeviceModel

    devices = DeviceModel.get_user_devices(user["user_id"])
    return {"devices": devices}


@router.post("/devices/heartbeat")
async def device_heartbeat(
    serial: str = Header(...),
    ip: str = Header(""),
    fw_version: str = Header(""),
):
    from models.database import DeviceModel

    DeviceModel.update_last_seen(serial, ip)
    if fw_version:
        DeviceModel.set_firmware_version(serial, fw_version)
    return {"status": "ok"}
