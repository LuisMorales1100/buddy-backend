from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select  # <-- NUEVO
from models.async_database import get_session, device_get_user_devices, device_upsert, DeviceModel  # <-- DeviceModel
from models.schemas import DeviceUpdate, DeviceResponse
from routes.auth import get_current_user

router = APIRouter(tags=["devices"])


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
        config=body.config,
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