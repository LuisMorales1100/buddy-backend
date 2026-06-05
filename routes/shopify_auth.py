import os
import secrets
import hashlib
import base64
import httpx
from urllib.parse import urlencode
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

router = APIRouter(prefix="/auth/shopify", tags=["Auth"])

SHOP_DOMAIN = os.getenv("SHOPIFY_SHOP_DOMAIN", "")
CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("SHOPIFY_REDIRECT_URI",
                         "https://api.buddyrobots.com/v1/auth/shopify/callback")
APP_ORIGIN = os.getenv("SHOPIFY_APP_ORIGIN", "https://buddyrobots.com")

# In-memory PKCE store keyed by state; use Redis in production
_pkce_store: dict[str, dict] = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def _get_auth_config() -> dict:
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://{SHOP_DOMAIN}/.well-known/openid-configuration")
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to discover Shopify auth endpoints")
        return r.json()


async def _exchange_code(code: str, code_verifier: str, auth_config: dict) -> dict:
    async with httpx.AsyncClient() as c:
        r = await c.post(auth_config["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        })
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to exchange authorization code")
        return r.json()


async def _get_user_info(access_token: str, auth_config: dict) -> dict:
    async with httpx.AsyncClient() as c:
        r = await c.get(
            auth_config.get("userinfo_endpoint", ""),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if r.status_code == 200:
            return r.json()
    return {}


async def _handle_auth(code: str, code_verifier: str, state: str, nonce: str):
    from routes.auth import create_access_token, create_refresh_token
    from models.database import UserModel, PurchaseModel, ProductModel

    auth_config = await _get_auth_config()
    token_data = await _exchange_code(code, code_verifier, auth_config)
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="No access token received")

    user_info = await _get_user_info(access_token, auth_config)
    email = user_info.get("email", "")
    customer_id = user_info.get("custom_user_id", "") or user_info.get("sub", "")
    name = user_info.get("name", "") or user_info.get("given_name", "")

    if not email:
        raise HTTPException(status_code=401, detail="Could not retrieve customer email")

    user = UserModel.get_by_email(email)
    if not user:
        user_id = UserModel.create(email, "", name)
    else:
        user_id = user["id"]

    if customer_id:
        UserModel.set_shopify_id(user_id, customer_id)

    PurchaseModel.link_to_user(email, user_id)
    has_purchase = PurchaseModel.user_has_purchase(user_id)

    if not has_purchase:
        raise HTTPException(
            status_code=402,
            detail="Necesitás una compra verificada de Buddy para acceder.",
        )

    products = ProductModel.get_user_products(user_id)
    payload = {"user_id": user_id, "email": email, "role": "user"}

    return {
        "token": create_access_token(payload),
        "refresh_token": create_refresh_token(payload),
        "user_id": user_id,
        "email": email,
        "name": name,
        "has_purchase": True,
        "products": products,
    }


@router.get("/discover")
async def discover():
    if not SHOP_DOMAIN or not CLIENT_ID:
        raise HTTPException(status_code=503, detail="Shopify OAuth not configured")

    auth_config = await _get_auth_config()

    state = secrets.token_hex(16)
    nonce = secrets.token_hex(16)
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())

    # Store PKCE params server-side for the GET callback to retrieve
    _pkce_store[state] = {"code_verifier": verifier, "nonce": nonce}

    params = urlencode({
        "scope": "openid email customer-account-api:full",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    return {
        "auth_url": f"{auth_config['authorization_endpoint']}?{params}",
        "state": state,
        "nonce": nonce,
        "code_verifier": verifier,
        "code_challenge": challenge,
    }


# POST callback for headless / in-app token exchange
class CallbackRequest(BaseModel):
    code: str
    code_verifier: str
    state: str
    nonce: str


@router.post("/callback")
async def callback_post(req: CallbackRequest):
    return await _handle_auth(req.code, req.code_verifier, req.state, req.nonce)


# GET callback for Shopify OAuth redirect (browser)
@router.get("/callback")
async def callback_get(
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    if error:
        err_html = f"""
        <html><body><h1>Error de autenticación</h1>
        <p>{error_description or error}</p>
        <a href="{APP_ORIGIN}">Volver a la app</a></body></html>
        """
        return HTMLResponse(err_html, status_code=400)

    if not code or not state:
        return HTMLResponse("<h1>Parámetros inválidos</h1>", status_code=400)

    # Retrieve PKCE params from store
    pkce = _pkce_store.pop(state, None)
    if not pkce:
        return HTMLResponse(
            "<h1>Estado inválido o expirado</h1>"
            f'<p><a href="{APP_ORIGIN}">Volver a la app</a></p>',
            status_code=400,
        )

    try:
        result = await _handle_auth(code, pkce["code_verifier"], state, pkce["nonce"])
    except HTTPException as e:
        return HTMLResponse(
            f"<html><body><h1>Error</h1><p>{e.detail}</p>"
            f'<a href="{APP_ORIGIN}">Volver a la app</a></body></html>',
            status_code=e.status_code,
        )
    except Exception as e:
        return HTMLResponse(
            f"<html><body><h1>Error interno</h1>"
            f'<a href="{APP_ORIGIN}">Volver a la app</a></body></html>',
            status_code=500,
        )

    # Success — redirect to PWA with tokens as query params
    params = urlencode({
        "token": result.get("token", ""),
        "refresh_token": result.get("refresh_token", ""),
        "user_id": result.get("user_id", ""),
        "email": result.get("email", ""),
        "name": result.get("name", ""),
        "has_purchase": "1",
    })
    pwa_url = f"{APP_ORIGIN}/app/?{params}"
    return RedirectResponse(url=pwa_url)
