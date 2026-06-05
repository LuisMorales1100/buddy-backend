import os
import hashlib
import hmac
import json
from fastapi import APIRouter, HTTPException, Request, Header, Depends
from typing import Optional

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
):
    from models.database import UserModel

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

    user = UserModel.get_by_email(email)
    if not user:
        user_id = UserModel.create(email, "", name)
        print(f"[Shopify] User created from webhook: {email}")
    else:
        user_id = user["id"]

    if customer_id:
        UserModel.set_shopify_id(user_id, customer_id)
        print(f"[Shopify] Customer {customer_id} linked to user {user_id}")

    return {"status": "ok", "email": email, "customer_id": customer_id}


@router.post("/webhook/order_create")
async def shopify_order_create(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(None),
    x_shopify_topic: Optional[str] = Header(None),
):
    from models.database import PurchaseModel, UserModel

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
    user = UserModel.get_by_email(email)
    if not user:
        UserModel.create(email, "", "")
        print(f"[Shopify] User auto-created from order: {email}")

    for item in line_items:
        sku = item.get("sku", "buddy_v1")
        name = item.get("title", "Buddy Assistant")
        PurchaseModel.create(
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
):
    body = await request.body()
    if not verify_shopify_webhook(body, x_shopify_hmac_sha256 or ""):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    data = json.loads(body)
    order_id = str(data.get("id", ""))
    financial_status = data.get("financial_status", "")

    if financial_status in ("paid", "fulfilled"):
        import sqlite3
        import pathlib

        conn = sqlite3.connect(
            pathlib.Path(__file__).parent.parent / "buddy.db"
        )
        conn.execute(
            "UPDATE purchases SET verified = 1 WHERE shopify_order_id = ?",
            (order_id,),
        )
        conn.commit()
        conn.close()
        print(f"[Shopify] Order {order_id} verified (paid)")

    return {"status": "ok", "order_id": order_id}


@router.post("/admin/verify-purchase")
async def admin_verify_purchase(email: str, _=Depends(require_admin)):
    from models.database import PurchaseModel, UserModel

    purchase = PurchaseModel.get_by_email(email)
    if not purchase:
        PurchaseModel.create(email=email, shopify_order_id=f"manual_{email}")
        purchase = PurchaseModel.get_by_email(email)

    user = UserModel.get_by_email(email)
    if user:
        PurchaseModel.link_to_user(email, user["id"])

    return {
        "status": "verified",
        "email": email,
        "has_user": user is not None,
    }
