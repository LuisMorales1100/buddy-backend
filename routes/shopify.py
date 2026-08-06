import os
import hashlib
import hmac
import json
from fastapi import APIRouter, HTTPException, Request, Header, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from models.async_database import get_session

router = APIRouter(prefix="/shopify", tags=["Shopify"])

ADMIN_KEY = os.getenv("ADMIN_KEY", "")
SHOPIFY_WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET", "")


async def require_admin(x_admin_key: Optional[str] = Header(None)):
    if not ADMIN_KEY:
        return True
    if not x_admin_key or x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")


def verify_shopify_webhook(body: bytes, hmac_header: str) -> bool:
    if not SHOPIFY_WEBHOOK_SECRET:
        print("[Shopify] WARNING: SHOPIFY_WEBHOOK_SECRET not set, skipping verification")
        return True
    digest = hmac.new(
        SHOPIFY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, hmac_header)


@router.post("/webhook/customers_create")
async def shopify_customer_create(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import user_get_by_email, user_create, user_set_shopify_id

    body = await request.body()
    if not verify_shopify_webhook(body, x_shopify_hmac_sha256 or ""):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    data = json.loads(body)
    email = data.get("email", "").strip().lower()
    customer_id = str(data.get("id", ""))
    first = data.get("first_name", "")
    last = data.get("last_name", "")
    name = f"{first} {last}".strip()

    if not email:
        return {"status": "skipped", "reason": "no email"}

    user = await user_get_by_email(session, email)
    if not user:
        user_id = await user_create(session, email, name)
        print(f"[Shopify] User created from webhook: {email}")
    else:
        user_id = user["id"]

    if customer_id:
        await user_set_shopify_id(session, user_id, customer_id)
        print(f"[Shopify] Customer {customer_id} linked to user {user_id}")

    return {"status": "ok", "email": email, "customer_id": customer_id}


@router.post("/webhook/order_create")
async def shopify_order_create(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(None),
    x_shopify_topic: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import user_get_by_email, user_create, purchase_create

    body = await request.body()
    data = json.loads(body)

    if not verify_shopify_webhook(body, x_shopify_hmac_sha256 or ""):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    email = data.get("email", "").strip().lower()
    order_id = str(data.get("id", ""))
    line_items = data.get("line_items", [])

    if not email or not order_id:
        raise HTTPException(status_code=400, detail="Missing email or order ID")

    # Create user if not exists
    user = await user_get_by_email(session, email)
    if not user:
        user_id = await user_create(session, email)
        print(f"[Shopify] User auto-created from order: {email}")

    for item in line_items:
        sku = item.get("sku", "buddy_v1")
        name = item.get("title", "Buddy Assistant")
        await purchase_create(
            session,
            email=email,
            shopify_order_id=order_id,
            product_sku=sku,
            product_name=name,
        )
        print(f"[Shopify] Purchase registered: {email} - {name} ({sku}) - Order {order_id}")

    return {"status": "ok", "email": email, "order_id": order_id}


@router.post("/webhook/order_updated")
async def shopify_order_updated(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import purchase_verify_by_order_id

    body = await request.body()
    if not verify_shopify_webhook(body, x_shopify_hmac_sha256 or ""):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    data = json.loads(body)
    order_id = str(data.get("id", ""))
    financial_status = data.get("financial_status", "")

    if financial_status in ("paid", "fulfilled"):
        await purchase_verify_by_order_id(session, order_id)
        print(f"[Shopify] Order {order_id} verified (paid)")

    return {"status": "ok", "order_id": order_id}


@router.post("/admin/verify-purchase")
async def admin_verify_purchase(
    email: str, _=Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    from models.async_database import purchase_get_by_email, purchase_create, user_get_by_email, purchase_link_to_user

    purchase = await purchase_get_by_email(session, email)
    if not purchase:
        await purchase_create(session, email=email, shopify_order_id=f"manual_{email}")
        purchase = await purchase_get_by_email(session, email)

    user = await user_get_by_email(session, email)
    if user:
        await purchase_link_to_user(session, email, user["id"])

    return {
        "status": "verified",
        "email": email,
        "has_user": user is not None,
    }
