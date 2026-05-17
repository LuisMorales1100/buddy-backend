import os
from fastapi import Header, HTTPException, status
from typing import Optional

JWT_SECRET = os.getenv("JWT_SECRET", "buddy_dev_secret")

async def buddy_auth(x_buddy_serial: Optional[str] = Header(None)):
    """Auth simple por serial. En producción: validar JWT contra DB."""
    serial = x_buddy_serial or "unknown"
    # TODO: validar licencia en DB
    return {"serial": serial}

def get_serial_from_body(body: dict) -> str:
    return body.get("serial", "unknown")