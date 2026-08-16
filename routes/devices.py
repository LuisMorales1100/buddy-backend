import ipaddress
import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.async_database import get_session, device_get_user_devices, device_upsert, DeviceModel
from models.schemas import DeviceUpdate, DeviceResponse
from routes.auth import get_current_user, decode_token

router = APIRouter(tags=["devices"])


@router.get("/devices/ping/{ip}")
async def ping_device(ip: str):
    """Proxy de ping al ESP32 (GET /api/status) para evitar Mixed Content
    cuando la app corre sobre HTTPS (ngrok/Capacitor): el WebView bloquea
    requests HTTP directas al dispositivo.
    Solo acepta IPs privadas/loopback para no funcionar como proxy abierto."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=422, detail="IP inválida")
    if not (addr.is_private or addr.is_loopback):
        raise HTTPException(status_code=422, detail="Solo IPs privadas (LAN)")
    url = f"http://{ip}/api/status"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                try:
                    device = response.json()
                except Exception:
                    device = None
                return {
                    "ip": ip,
                    "status": "online",
                    "http_status": response.status_code,
                    "device": device,
                }
            return {"ip": ip, "status": "offline", "http_status": response.status_code}
    except httpx.ConnectError:
        return {"ip": ip, "status": "offline", "error": "connect_error"}
    except httpx.TimeoutException:
        return {"ip": ip, "status": "offline", "error": "timeout"}
    except Exception as e:
        return {"ip": ip, "status": "offline", "error": str(e)[:100]}


@router.get("/devices/esp32-proxy/{ip}/{path:path}")
@router.post("/devices/esp32-proxy/{ip}/{path:path}")
async def esp32_proxy(ip: str, path: str, request: Request):
    """Proxy general al ESP32 (HTTP -> HTTP) para evitar Mixed Content
    cuando la app corre sobre HTTPS (ngrok/Capacitor). Forwardea cualquier
    método/endpoint a http://{ip}/{path}. Solo IPs privadas/loopback
    (guard SSRF centralizado)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=422, detail="IP inválida")
    if not (addr.is_private or addr.is_loopback):
        raise HTTPException(status_code=422, detail="Solo IPs privadas (LAN)")
    url = f"http://{ip}/{path}"
    body = await request.body() if request.method == "POST" else None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.request(
                request.method,
                url,
                content=body,
                headers={
                    "Content-Type": request.headers.get("Content-Type", "application/json"),
                },
            )
            content_type = response.headers.get("content-type", "")
            payload = response.json() if content_type.startswith("application/json") and response.content else None
            return JSONResponse(content=payload, status_code=response.status_code)
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="connect_error")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:100])


@router.get("/devices", response_model=list[DeviceResponse])
async def list_devices(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    user_id = user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    devices = await device_get_user_devices(session, int(user_id))
    return [
        DeviceResponse(
            serial=d["serial"],
            name=d.get("name"),
            last_known_ip=d.get("last_known_ip"),
            config=d.get("config", {}),
            created_at=str(d["created_at"]) if d.get("created_at") else None,
        )
        for d in devices
    ]

# FIX: Nuevo endpoint para que el ESP32 jale su config con device token
@router.get("/devices/{serial}", response_model=DeviceResponse)
async def get_device(
    serial: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_session),
):
    """Endpoint para que el ESP32 obtenga su configuración.
    Soporta auth por device token (role: 'device') o user token."""
    
    # Intentar auth por device token primero
    device_token_valid = False
    token_serial = None
    
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = decode_token(token)
        if payload and payload.get("role") == "device":
            device_token_valid = True
            token_serial = payload.get("serial")
    
    result = await session.execute(
        select(DeviceModel).where(DeviceModel.serial == serial)
    )
    device = result.scalar_one_or_none()
    
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Si es device token, solo puede leer su propio serial
    if device_token_valid and token_serial != serial:
        raise HTTPException(status_code=403, detail="Device can only access its own config")
    
    # Si no es device token, requerir user auth
    if not device_token_valid:
        # Fallback a get_current_user (requiere user token)
        # Nota: FastAPI no permite múltiples Depends fácilmente aquí,
        # así que verificamos manualmente si hay user_id en el token
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
            payload = decode_token(token)
            if not payload or not payload.get("user_id"):
                raise HTTPException(status_code=401, detail="Invalid token")
            if device.user_id != int(payload["user_id"]):
                raise HTTPException(status_code=403, detail="Not your device")
        else:
            raise HTTPException(status_code=401, detail="Authentication required")
    
    return DeviceResponse(
        serial=device.serial,
        name=device.name,
        last_known_ip=device.last_known_ip,
        config=device.config or {},
        created_at=str(device.created_at) if device.created_at else None,
    )


@router.put("/devices/{serial}", response_model=DeviceResponse)
async def upsert_device(
    serial: str,
    body: DeviceUpdate,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    user_id = user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    device = await device_upsert(
        session,
        serial=serial,
        user_id=int(user_id),
        name=body.name,
        last_known_ip=body.last_known_ip,
        config=body.config.model_dump(mode="json") if body.config else None,
    )
    return DeviceResponse(
        serial=device["serial"],
        name=device.get("name"),
        last_known_ip=device.get("last_known_ip"),
        config=device.get("config", {}),
        created_at=str(device["created_at"]) if device.get("created_at") else None,
    )


@router.delete("/devices/{serial}")
async def delete_device(
    serial: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    user_id = user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await session.execute(
        select(DeviceModel).where(
            DeviceModel.serial == serial,
            DeviceModel.user_id == int(user_id),
        )
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await session.delete(device)
    await session.commit()
    return {"ok": True}