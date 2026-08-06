import os
import hashlib
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Header, Depends, Response, Request
from pydantic import BaseModel
from typing import Optional
from jose import jwt as jose_jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from services.limiter import limiter
from services.cache import cache
from services.email_sender import send_otp_email
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


class RequestCodeRequest(BaseModel):
    email: str


class VerifyCodeRequest(BaseModel):
    email: str
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str


class PairDeviceRequest(BaseModel):
    serial: str
    name: str = "Buddy"


class DeviceRegisterRequest(BaseModel):
    serial: str
    name: str = "Buddy"


# ---- Login OTP por email ----
OTP_TTL_SECONDS = 600            # 10 min
OTP_INTENTS_PER_CODE = 5         # máx intentos antes de invalidar
OTP_MAX_PER_EMAIL_HOUR = 3       # máx códigos enviados por email/hora

# OTP fijo SOLO para emails de test (dominios configurados). En producción,
# los emails reales siempre usan código random + SMTP.
TEST_OTP_CODE = os.getenv("TEST_OTP_CODE", "123456")
TEST_EMAIL_DOMAINS = [
    d.strip().lower()
    for d in os.getenv("TEST_EMAIL_DOMAINS", "@buddy.local,@test.com").split(",")
    if d.strip()
]

def _is_test_email(email: str) -> bool:
    return any(email.endswith(domain) for domain in TEST_EMAIL_DOMAINS)

def _gen_otp() -> str:
    # 6 dígitos numéricos, libres de ambigüedad al leerlos por voz/escritura.
    for _ in range(10):
        code = f"{secrets.randbelow(1000000):06d}"
        if code[0] != "0":
            return code
    return f"{secrets.randbelow(1000000):06d}"


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


# ===== Routes =====

@router.post("/auth/request-code")
@limiter.limit("5/minute")
async def request_code(
    request: Request, req: RequestCodeRequest, response: Response,
):
    email = req.email.strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Email inválido")

    # Limitar códigos enviados por email para frustrar spam de login.
    rate_key = f"otp_rate:{email}"
    sent = cache.get(rate_key)
    if sent and sent["count"] >= OTP_MAX_PER_EMAIL_HOUR:
        raise HTTPException(
            status_code=429,
            detail="Demasiados códigos enviados. Esperá una hora e intentá de nuevo.",
        )

    # Para emails de test (dominios configurados): OTP fijo, sin enviar email.
    if _is_test_email(email):
        code = TEST_OTP_CODE
        print(f"[TEST OTP] {email} -> {code}")
    else:
        code = _gen_otp()

    # Guardamos SOLO el hash (defensa ante lectura de memoria), no el código.
    cache.set(
        f"otp:{email}",
        {"hash": hashlib.sha256(code.encode()).hexdigest(), "intents": OTP_INTENTS_PER_CODE},
        ttl=OTP_TTL_SECONDS,
    )

    count = (sent["count"] if sent else 0) + 1
    cache.set(rate_key, {"count": count}, ttl=3600)

    if not _is_test_email(email):
        await send_otp_email(email, code)

    return {"message": "Code sent", "expires_in": OTP_TTL_SECONDS}


@router.post("/auth/verify-code")
@limiter.limit("10/minute")
async def verify_code(
    request: Request, req: VerifyCodeRequest, response: Response,
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import user_get_by_email, user_create

    email = req.email.strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Email inválido")

    record = cache.get(f"otp:{email}")
    if not record:
        raise HTTPException(status_code=401, detail="Código inválido o expirado. Pedí uno nuevo.")

    if record["hash"] != hashlib.sha256(req.code.strip().encode()).hexdigest():
        record["intents"] -= 1
        if record["intents"] <= 0:
            cache.delete(f"otp:{email}")
            raise HTTPException(status_code=401, detail="Código incorrecto. Solicitalo de nuevo.")
        cache.set(f"otp:{email}", record, ttl=OTP_TTL_SECONDS)
        raise HTTPException(status_code=401, detail="Código incorrecto.")

    cache.delete(f"otp:{email}")

    # Verificar que el código coincide ES el login: buscamos (o creamos) el usuario.
    user = await user_get_by_email(session, email)
    if user:
        user_id = user["id"]
        name = user["name"]
    else:
        user_id = await user_create(session, email)
        name = ""

    payload = {"user_id": user_id, "email": email, "role": "user"}
    access_token = create_access_token(payload)
    refresh_token = create_refresh_token(payload)

    set_auth_cookies(response, access_token, refresh_token)

    return {
        "token": access_token,
        "refresh_token": refresh_token,
        "user_id": user_id,
        "email": email,
        "name": name,
    }


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
