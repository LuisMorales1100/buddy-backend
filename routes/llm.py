import os
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from models.schemas import LLMRequest, LLMResponse
from routes.auth import decode_token
from typing import Optional

router = APIRouter(prefix="/llm", tags=["LLM"])

BUDDY_CLOUD_URL = os.getenv("BUDDY_CLOUD_LLM_URL", "")

# Sanitize API keys from error messages (never log/return user keys)
API_KEY_PATTERNS = ["sk-", "sk-ant-", "x-api-key", "Bearer "]


def sanitize(msg: str) -> str:
    for pat in API_KEY_PATTERNS:
        if pat in msg:
            msg = msg.replace(pat, "[REDACTED]")
    return msg[:500]


async def get_llm_auth(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = decode_token(token)
        if payload:
            return payload
    return {"role": "anonymous", "serial": "unknown"}


@router.post("/chat", response_model=LLMResponse)
async def chat(request: LLMRequest, auth=Depends(get_llm_auth)):
    from models.database import PurchaseModel, UsageModel

    user_id = auth.get("user_id")
    role = auth.get("role", "anonymous")

    # Anonymous users are blocked
    if role == "anonymous" or not user_id:
        raise HTTPException(
            status_code=402,
            detail="Debes iniciar sesión con una cuenta que haya comprado Buddy para usar Buddy Cloud LLM.",
        )

    # Check daily usage limit
    can_request, msg = UsageModel.can_make_request(user_id)
    if not can_request:
        has_purchase = PurchaseModel.user_has_purchase(user_id)
        limit_msg = (
            "Has alcanzado el límite diario de Buddy Cloud. Vuelve mañana."
            if has_purchase
            else "Límite diario de prueba alcanzado (20 mensajes). Vinculá tu cuenta con el email de tu compra de Buddy para acceso ilimitado."
        )
        raise HTTPException(status_code=429, detail=limit_msg)

    # Buddy Cloud LLM requires purchase verification
    if request.provider == "buddy_cloud":
        has_purchase = PurchaseModel.user_has_purchase(user_id)
        if not has_purchase:
            raise HTTPException(
                status_code=402,
                detail="Necesitás una compra verificada de Buddy para usar nuestro modelo. Iniciá sesión con el email que usaste al comprar.",
            )

    # Increment usage counter
    UsageModel.increment(user_id)

    try:
        if request.provider == "openai":
            return await proxy_openai(request)
        elif request.provider == "anthropic":
            return await proxy_anthropic(request)
        elif request.provider == "ollama":
            return await proxy_ollama(request)
        elif request.provider == "buddy_cloud":
            return await proxy_buddy_cloud(request)
        elif request.provider in ("local", "custom"):
            return await proxy_custom(request)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {request.provider}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=sanitize(str(e)))


async def proxy_openai(request: LLMRequest) -> LLMResponse:
    api_key = request.api_key or OPENAI_KEY
    if not api_key:
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")

    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": request.model or "gpt-4o-mini",
                "messages": messages,
                "temperature": request.temperature,
                "stream": False,
            },
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=sanitize(res.text))

        data = res.json()
        return LLMResponse(
            text=data["choices"][0]["message"]["content"],
            provider="openai",
            model=data.get("model"),
            usage=data.get("usage"),
        )


async def proxy_anthropic(request: LLMRequest) -> LLMResponse:
    api_key = request.api_key or ANTHROPIC_KEY
    if not api_key:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    system_msg = next((m for m in request.messages if m.role == "system"), None)
    user_msgs = [m for m in request.messages if m.role != "system"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": request.model or "claude-3-haiku-20240307",
                "max_tokens": 1024,
                "system": system_msg.content if system_msg else "",
                "messages": [{"role": m.role, "content": m.content} for m in user_msgs],
                "temperature": request.temperature,
            },
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=sanitize(res.text))

        data = res.json()
        return LLMResponse(
            text=data["content"][0]["text"],
            provider="anthropic",
            model=data.get("model"),
            usage=data.get("usage"),
        )


async def proxy_ollama(request: LLMRequest) -> LLMResponse:
    endpoint = request.endpoint or "http://localhost:11434"
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            f"{endpoint}/api/chat",
            json={
                "model": request.model or "llama3",
                "messages": messages,
                "stream": False,
                "options": {"temperature": request.temperature},
            },
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=sanitize(res.text))

        data = res.json()
        return LLMResponse(
            text=data.get("message", {}).get("content", ""),
            provider="ollama",
            model=data.get("model"),
        )


async def proxy_custom(request: LLMRequest) -> LLMResponse:
    if not request.endpoint:
        raise HTTPException(status_code=400, detail="Custom endpoint required")

    headers = {"Content-Type": "application/json"}
    if request.api_key:
        headers["Authorization"] = f"Bearer {request.api_key}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            request.endpoint,
            headers=headers,
            json={
                "model": request.model,
                "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                "temperature": request.temperature,
            },
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=sanitize(res.text))

        data = res.json()
        text = (
            data.get("response")
            or data.get("reply")
            or data.get("text")
            or data.get("message", {}).get("content", "")
            or str(data)
        )
        return LLMResponse(text=text, provider="custom")


async def proxy_buddy_cloud(request: LLMRequest) -> LLMResponse:
    if not BUDDY_CLOUD_URL:
        raise HTTPException(status_code=503, detail="Buddy Cloud LLM no está configurado")

    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            BUDDY_CLOUD_URL,
            json={
                "model": request.model or "buddy-llm",
                "messages": messages,
                "temperature": request.temperature,
            },
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=sanitize(res.text))

        data = res.json()
        text = (
            data.get("response")
            or data.get("reply")
            or data.get("text")
            or data.get("message", {}).get("content", "")
            or str(data)
        )
        return LLMResponse(text=text, provider="buddy_cloud", model=data.get("model"))
