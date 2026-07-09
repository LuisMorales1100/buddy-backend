import os
import hashlib
import secrets
import bcrypt
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Header, Depends, Response, Request
from pydantic import BaseModel
from typing import Optional
from jose import jwt as jose_jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from services.limiter import limiter
from models.async_database import get_session

JWT_SECRET = os.getenv("JWT_SECRET", "")
REFRESH_SECRET = os.getenv("REFRESH_SECRET", "")
ENV = os.getenv("ENV", "development")

if not JWT_SECRET or not REFRESH_SECRET:
    if ENV == "production":
        raise RuntimeError(
            "JWT_SECRET and REFRESH_SECRET must be set in production. "
            "Generate them with: openssl rand -hex 32"
        )
    print("[WARN] JWT_SECRET/REFRESH_SECRET not set — using dev-only fallback")
    JWT_SECRET = "dev_only_insecure_" + os.urandom(16).hex()
    REFRESH_SECRET = "dev_only_insecure_" + os.urandom(16).hex()

if ENV == "production" and JWT_SECRET in (
    "buddy_super_secret_key_cambiar_en_produccion",
    "buddy_dev_secret_change_in_production",
    "",
):
    raise RuntimeError(
        "JWT_SECRET is still set to the default value. "
        "Generate a new one with: openssl rand -hex 32"
    )

TOKEN_EXPIRE_MINUTES = 120
REFRESH_TOKEN_EXPIRE_DAYS = 30
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", "")
COOKIE_SECURE = os.getenv("ENV", "development") == "production"

router = APIRouter(prefix="", tags=["Auth"])


def set_auth_cookies(response: Response, access_token: str, refresh_token: str):
    """Set httpOnly auth cookies on the response.
    
    Browser clients receive httpOnly cookies (XSS-safe).
    Non-browser clients (ESP32) continue using the JSON body + Authorization header.
    """
    secure = COOKIE_SECURE
    samesite = "none" if secure else "lax"
    
    response.set_cookie(
        key="buddy_token",
        value=access_token,
        max_age=TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=COOKIE_DOMAIN if secure else None,
        path="/",
    )
    response.set_cookie(
        key="buddy_refresh",
        value=refresh_token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=COOKIE_DOMAIN if secure else None,
        path="/v1/auth/refresh",
    )


def clear_auth_cookies(response: Response):
    """Clear auth cookies on logout."""
    for cookie in ("buddy_token", "buddy_refresh"):
        response.set_cookie(
            key=cookie,
            value="",
            max_age=0,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            domain=COOKIE_DOMAIN if COOKIE_SECURE else None,
            path="/",
        )


def get_token_from_request(request: Request, authorization: Optional[str] = None) -> Optional[str]:
    """Extract token: try cookie first, then Authorization header.
    
    This allows browser clients to use httpOnly cookies (XSS-safe),
    while device/API clients continue using the Authorization header.
    """
    token = request.cookies.get("buddy_token")
    if token:
        return token
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None


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


# ---- JWT usando python-jose (HMAC-SHA256 estándar) ----

def _encode_jwt(payload: dict, secret: str) -> str:
    return jose_jwt.encode(payload, secret, algorithm="HS256")


def _decode_jwt(token: str, secret: str) -> Optional[dict]:
    try:
        payload = jose_jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("exp", 0) < datetime.utcnow().timestamp():
            return None
        return payload
    except JWTError:
        return None


def _verify_legacy(token: str, secret: str) -> Optional[dict]:
    """Verifica tokens creados con la implementación anterior (pre-python-jose)."""
    try:
        import base64 as b64
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = __import__("json").loads(
            b64.urlsafe_b64decode(parts[1] + "==").decode()
        )
        sig_input = f"{parts[0]}.{parts[1]}"
        expected_sig = hashlib.sha256((sig_input + secret).encode()).hexdigest()
        sig_b64 = b64.urlsafe_b64encode(expected_sig.encode()).rstrip(b"=").decode()
        if sig_b64 != parts[2]:
            return None
        if payload.get("exp", 0) < datetime.utcnow().timestamp():
            return None
        return payload
    except Exception:
        return None


def _verify(token: str, secret: str) -> Optional[dict]:
    """Intenta nuevo método primero, fallback a legacy para migración."""
    payload = _decode_jwt(token, secret)
    if payload:
        return payload
    return _verify_legacy(token, secret)


def create_access_token(payload: dict) -> str:
    exp = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    token_payload = {
        **payload,
        "exp": int(exp.timestamp()),
        "iat": int(datetime.utcnow().timestamp()),
        "type": "access",
    }
    return _encode_jwt(token_payload, JWT_SECRET)


def create_refresh_token(payload: dict) -> str:
    exp = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    token_payload = {
        **payload,
        "exp": int(exp.timestamp()),
        "iat": int(datetime.utcnow().timestamp()),
        "type": "refresh",
    }
    return _encode_jwt(token_payload, REFRESH_SECRET)


def decode_token(token: str) -> Optional[dict]:
    return _verify(token, JWT_SECRET)


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session),
):
    """Get current user from cookie (browser) or Authorization header (API/device)."""
    token = get_token_from_request(request, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("user_id")
    if user_id:
        from models.async_database import user_get_by_id
        db_user = await user_get_by_id(session, int(user_id))
        if not db_user:
            raise HTTPException(status_code=401, detail="User not found or invalid session")
    return payload


# ===== Internal registration (webhook-only) =====
async def internal_register(session: AsyncSession, email: str, name: str = "") -> Optional[int]:
    from models.async_database import user_create
    return await user_create(session, email, "", name)


# ===== Routes =====

@router.post("/auth/login")
@limiter.limit("10/minute")
async def login(
    request: Request, req: LoginRequest, response: Response,
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import (
        user_get_by_email, purchase_link_to_user, purchase_user_has_purchase, product_get_user_products,
    )

    user = await user_get_by_email(session, req.email)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    if not user.get("password_set", 0):
        raise HTTPException(
            status_code=401,
            detail="Debés configurar tu contraseña primero. Revisá tu email.",
        )

    await purchase_link_to_user(session, req.email, user["id"])
    has_purchase = await purchase_user_has_purchase(session, user["id"])

    if not has_purchase:
        raise HTTPException(
            status_code=402,
            detail="Necesitás una compra verificada de Buddy para acceder.",
        )

    products = await product_get_user_products(session, user["id"])

    payload = {"user_id": user["id"], "email": user["email"], "role": "user"}
    access_token = create_access_token(payload)
    refresh_token = create_refresh_token(payload)

    # Set httpOnly cookies for browser clients (XSS-safe)
    set_auth_cookies(response, access_token, refresh_token)

    return {
        "token": access_token,
        "refresh_token": refresh_token,
        "user_id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "has_purchase": True,
        "products": products,
    }


@router.post("/auth/set-password")
async def set_password(
    req: SetPasswordRequest,
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import user_get_by_email, user_set_password

    expected = hashlib.sha256((req.email + JWT_SECRET).encode()).hexdigest()[:12]
    if req.code != expected:
        raise HTTPException(status_code=401, detail="Código inválido o expirado")

    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    user = await user_get_by_email(session, req.email)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    await user_set_password(session, user["id"], hash_password(req.password))

    return {"status": "ok", "message": "Contraseña configurada. Ya podés iniciar sesión."}


@router.post("/auth/refresh")
async def refresh(req: RefreshRequest, response: Response, request: Request):
    # Try refresh from cookie first, then request body
    refresh_token = request.cookies.get("buddy_refresh") or req.refresh_token
    payload = _verify(refresh_token, REFRESH_SECRET)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    fresh_payload = {
        "user_id": payload["user_id"],
        "email": payload["email"],
        "role": payload.get("role", "user"),
    }
    access_token = create_access_token(fresh_payload)
    new_refresh_token = create_refresh_token(fresh_payload)

    set_auth_cookies(response, access_token, new_refresh_token)

    return {
        "token": access_token,
        "refresh_token": new_refresh_token,
    }


@router.post("/auth/logout")
async def logout(response: Response):
    """Clear auth cookies. Client should also clear localStorage tokens."""
    clear_auth_cookies(response)
    return {"status": "ok", "message": "Sesión cerrada"}


@router.get("/auth/me")
async def me(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import (
        user_get_by_id, device_get_user_devices, purchase_user_has_purchase, product_get_user_products,
    )

    db_user = await user_get_by_id(session, user["user_id"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    devices = await device_get_user_devices(session, user["user_id"])
    has_purchase = await purchase_user_has_purchase(session, user["user_id"])
    products = await product_get_user_products(session, user["user_id"]) if has_purchase else []

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
async def register_device(
    req: DeviceRegisterRequest,
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import device_upsert

    await device_upsert(session, serial=req.serial, name=req.name)

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
    req: PairDeviceRequest, user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import device_upsert

    await device_upsert(session, serial=req.serial, user_id=user["user_id"], name=req.name)
    return {"status": "paired", "serial": req.serial, "user_id": user["user_id"]}


@router.post("/devices/heartbeat")
async def device_heartbeat(
    serial: str = Header(...),
    ip: str = Header(""),
    fw_version: str = Header(""),
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import device_upsert

    await device_upsert(session, serial=serial, last_known_ip=ip or None)
    return {"status": "ok"}
