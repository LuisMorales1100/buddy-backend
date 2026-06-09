import os
import pathlib
from fastapi import APIRouter, HTTPException, Header, Depends
from fastapi.responses import FileResponse, JSONResponse
from models.schemas import OTACheckResponse
from models.database import DeviceModel
from typing import Optional

router = APIRouter(prefix="/firmware", tags=["OTA"])

ADMIN_KEY = os.getenv("ADMIN_KEY", "")


async def require_admin(x_admin_key: Optional[str] = Header(None)):
    if not ADMIN_KEY:
        return True
    if not x_admin_key or x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")

DOMAIN = os.getenv("DOMAIN", "api.buddyrobots.com")
FIRMWARE_DIR = pathlib.Path(__file__).parent.parent / "public" / "firmware"
os.makedirs(FIRMWARE_DIR, exist_ok=True)


@router.get("/check", response_model=OTACheckResponse)
async def check_update(version: str = "0.0", serial: str = ""):
    # Get latest firmware from database
    import sqlite3
    conn = sqlite3.connect(
        pathlib.Path(__file__).parent.parent / "buddy.db"
    )
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM firmware_releases ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if row:
        current = row["version"]
        changelog = row["changelog"]
        critical = bool(row["critical"])
    else:
        current = "4.0"
        changelog = "Initial OTA release"
        critical = False

    needs = version != current

    return OTACheckResponse(
        current=current,
        needs_update=needs,
        url=f"https://{DOMAIN}/v1/firmware/download?version={current}" if needs else None,
        changelog=changelog if needs else None,
        critical=critical,
    )


@router.get("/download")
async def download_firmware(version: str = "4.0"):
    file_path = FIRMWARE_DIR / f"buddy_firmware_v{version}.bin"
    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Firmware version {version} not found. Available: {[f.name for f in FIRMWARE_DIR.glob('*.bin')]}",
        )

    return FileResponse(
        path=file_path,
        filename=f"buddy_firmware_v{version}.bin",
        media_type="application/octet-stream",
    )


@router.get("/releases")
async def list_releases():
    import sqlite3
    conn = sqlite3.connect(
        pathlib.Path(__file__).parent.parent / "buddy.db"
    )
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT version, changelog, file_size, critical, created_at FROM firmware_releases ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return {"releases": [dict(r) for r in rows]}


@router.post("/admin/upload")
async def upload_firmware(version: str, changelog: str = "", critical: bool = False, _=Depends(require_admin)):
    """Admin endpoint to register a new firmware version.
    The .bin file must already exist in public/firmware/.
    """
    import sqlite3
    file_path = FIRMWARE_DIR / f"buddy_firmware_v{version}.bin"
    if not file_path.exists():
        raise HTTPException(status_code=400, detail=f"File buddy_firmware_v{version}.bin not found in {FIRMWARE_DIR}")

    file_size = file_path.stat().st_size
    conn = sqlite3.connect(
        pathlib.Path(__file__).parent.parent / "buddy.db"
    )
    try:
        conn.execute(
            "INSERT INTO firmware_releases (version, changelog, file_path, file_size, critical) VALUES (?, ?, ?, ?, ?)",
            (version, changelog, str(file_path), file_size, int(critical)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=409, detail=f"Version {version} already exists")
    conn.close()

    return {
        "status": "registered",
        "version": version,
        "file_size": file_size,
        "critical": critical,
    }
