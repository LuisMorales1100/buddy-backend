import os
import hashlib
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional

JWT_SECRET = os.getenv("JWT_SECRET", "buddy_dev_secret_change_in_production")
JWT_ALGO = "HS256"
TOKEN_EXPIRE_DAYS = 365

router = APIRouter(prefix="", tags=["Auth"])


# ===== Models =====
class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class PairDeviceRequest(BaseModel):
    serial: str
    name: str = "Buddy"


class DeviceRegisterRequest(BaseModel):
    serial: str
    name: str = "Buddy"
    firmware_version: str = "4.0"
    mac: str = ""


# ===== Password Hashing (SHA-256 + salt) =====
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split(":")
    if len(parts) != 2:
        return False
    salt, expected = parts
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return h == expected


# ===== JWT =====
def create_token(payload: dict, expires_days: int = TOKEN_EXPIRE_DAYS) -> str:
    header = '{"alg":"HS256","typ":"JWT"}'
    exp = (datetime.utcnow() + timedelta(days=expires_days)).timestamp()
    payload["exp"] = int(exp)
    payload["iat"] = int(datetime.utcnow().timestamp())

    def b64encode(data: bytes) -> str:
        import base64
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header_b64 = b64encode(header.encode())
    payload_b64 = b64encode(
        __import__("json").dumps(payload, separators=(",", ":")).encode()
    )

    signature_input = f"{header_b64}.{payload_b64}"
    sig = hashlib.sha256((signature_input + JWT_SECRET).encode()).hexdigest()
    sig_b64 = b64encode(sig.encode())

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_token(token: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        import base64, json

        def b64decode(s: str) -> bytes:
            padding = 4 - len(s) % 4
            if padding != 4:
                s += "=" * padding
            return base64.urlsafe_b64decode(s)

        header = json.loads(b64decode(parts[0]))
        payload = json.loads(b64decode(parts[1]))

        # Verify signature
        sig_input = f"{parts[0]}.{parts[1]}"
        expected_sig = hashlib.sha256((sig_input + JWT_SECRET).encode()).hexdigest()
        sig_b64 = base64.urlsafe_b64encode(expected_sig.encode()).rstrip(b"=").decode()

        if sig_b64 != parts[2]:
            return None

        # Check expiry
        if payload.get("exp", 0) < datetime.utcnow().timestamp():
            return None

        return payload
    except Exception:
        return None


# ===== Auth Dependency =====
async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


# ===== Routes =====

@router.post("/auth/register")
async def register(req: RegisterRequest):
    from models.database import UserModel, PurchaseModel

    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    user_id = UserModel.create(req.email, hash_password(req.password), req.name)
    if user_id is None:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Auto-link purchase if email matches Shopify order
    PurchaseModel.link_to_user(req.email, user_id)
    has_purchase = PurchaseModel.user_has_purchase(user_id)

    token = create_token({"user_id": user_id, "email": req.email, "role": "user"})
    return {"token": token, "user_id": user_id, "email": req.email, "name": req.name, "has_purchase": has_purchase}


@router.post("/auth/login")
async def login(req: LoginRequest):
    from models.database import UserModel, PurchaseModel

    user = UserModel.get_by_email(req.email)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Auto-link purchase on login too
    PurchaseModel.link_to_user(req.email, user["id"])
    has_purchase = PurchaseModel.user_has_purchase(user["id"])

    token = create_token(
        {"user_id": user["id"], "email": user["email"], "role": "user"}
    )
    return {
        "token": token,
        "user_id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "has_purchase": has_purchase,
    }


@router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    from models.database import UserModel, DeviceModel, PurchaseModel

    db_user = UserModel.get_by_id(user["user_id"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    devices = DeviceModel.get_user_devices(user["user_id"])
    has_purchase = PurchaseModel.user_has_purchase(user["user_id"])

    return {
        "user_id": db_user["id"],
        "email": db_user["email"],
        "name": db_user["name"],
        "shopify_customer_id": db_user.get("shopify_customer_id"),
        "has_purchase": has_purchase,
        "devices": devices,
    }


@router.post("/devices/register")
async def register_device(req: DeviceRegisterRequest):
    from models.database import DeviceModel

    DeviceModel.register(req.serial, req.name, req.firmware_version, req.mac)
    DeviceModel.update_last_seen(req.serial, "", online=True)

    token = create_token(
        {"serial": req.serial, "role": "device"}, expires_days=TOKEN_EXPIRE_DAYS
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
