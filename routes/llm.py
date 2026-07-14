import os
import json
import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
from models.schemas import LLMRequest, LLMResponse
from routes.auth import decode_token
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from models.async_database import get_session
from models.async_database import usage_can_make_request, purchase_user_has_purchase, usage_increment

log = structlog.get_logger()

router = APIRouter(prefix="/llm", tags=["LLM"])

BUDDY_CLOUD_URL = os.getenv("BUDDY_CLOUD_LLM_URL", "")
LLM_SERVICE_API_KEY = os.getenv("LLM_SERVICE_API_KEY", "")
FALLBACK_PROVIDER = os.getenv("FALLBACK_PROVIDER", "")
FALLBACK_API_KEY = os.getenv("FALLBACK_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

API_KEY_PATTERNS = ["sk-", "sk-ant-", "x-api-key", "Bearer "]


def sanitize(msg: str) -> str:
    for pat in API_KEY_PATTERNS:
        if pat in msg:
            msg = msg.replace(pat, "[REDACTED]")
    return msg[:500]


@router.get("/health")
async def llm_health():
    if not BUDDY_CLOUD_URL:
        raise HTTPException(status_code=503, detail="LLM service URL not configured")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(BUDDY_CLOUD_URL)
            if res.status_code < 500:
                return {"status": "ok", "llm_service": BUDDY_CLOUD_URL}
            raise HTTPException(status_code=502, detail=f"LLM service returned {res.status_code}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="LLM service timed out")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="LLM service unreachable")


async def get_llm_auth(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = decode_token(token)
        if payload:
            return payload
    return {"role": "anonymous", "serial": "unknown"}


async def _usage_check(request: LLMRequest, auth: dict, session: AsyncSession):
    if len(request.messages) > 20:
        raise HTTPException(status_code=400, detail="Demasiados mensajes en la solicitud (máx. 20)")

    user_id = auth.get("user_id")
    role = auth.get("role", "anonymous")

    if role == "anonymous" or not user_id:
        raise HTTPException(
            status_code=402,
            detail="Debes iniciar sesión con una cuenta que haya comprado Buddy para usar Buddy Cloud LLM.",
        )

    can_request, msg = await usage_can_make_request(session, user_id)
    if not can_request:
        has_purchase = await purchase_user_has_purchase(session, user_id)
        limit_msg = (
            "Has alcanzado el límite diario de Buddy Cloud. Vuelve mañana."
            if has_purchase
            else "Límite diario de prueba alcanzado (20 mensajes). Vinculá tu cuenta con el email de tu compra de Buddy para acceso ilimitado."
        )
        raise HTTPException(status_code=429, detail=limit_msg)

    if request.provider == "buddy_cloud":
        has_purchase = await purchase_user_has_purchase(session, user_id)
        if not has_purchase:
            raise HTTPException(
                status_code=402,
                detail="Necesitás una compra verificada de Buddy para usar nuestro modelo. Iniciá sesión con el email que usaste al comprar.",
            )

    await usage_increment(session, user_id)


@router.post("/chat", response_model=LLMResponse)
async def chat(
    request: LLMRequest, auth=Depends(get_llm_auth),
    session: AsyncSession = Depends(get_session),
):
    log.info("chat.request", provider=request.provider, prompt=request.messages[-1].content[:80] if request.messages else "", user_id=auth.get("user_id"))
    await _usage_check(request, auth, session)

    if BUDDY_CLOUD_URL and request.provider == "buddy_cloud":
        return await proxy_buddy_cloud(request)

    errors = []

    async def try_provider(provider: str) -> LLMResponse:
        req = request.model_copy(deep=True)
        req.provider = provider
        if not req.api_key and provider != "buddy_cloud":
            req.api_key = FALLBACK_API_KEY
        if provider == "openai":
            return await proxy_openai(req)
        elif provider == "anthropic":
            return await proxy_anthropic(req)
        elif provider == "ollama":
            return await proxy_ollama(req)
        elif provider == "buddy_cloud":
            return await proxy_buddy_cloud(req)
        elif provider in ("local", "custom"):
            return await proxy_custom(req)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    providers_to_try = [request.provider]
    if request.provider == "buddy_cloud" and FALLBACK_PROVIDER:
        providers_to_try.append(FALLBACK_PROVIDER)

    for provider in providers_to_try:
        try:
            return await try_provider(provider)
        except HTTPException as e:
            if e.status_code < 500 or provider == providers_to_try[-1]:
                raise
            errors.append(f"{provider}: {e.detail}")
            continue
        except Exception as e:
            if provider == providers_to_try[-1]:
                raise HTTPException(status_code=502, detail=sanitize(str(e)))
            errors.append(f"{provider}: {sanitize(str(e))}")
            continue

    raise HTTPException(
        status_code=502,
        detail=f"All providers failed: {'; '.join(errors)}",
    )


@router.post("/chat/stream")
async def chat_stream(
    request: LLMRequest, request_obj: Request, auth=Depends(get_llm_auth),
    session: AsyncSession = Depends(get_session),
):
    log.info("chat.stream_request", provider=request.provider, prompt=request.messages[-1].content[:80] if request.messages else "", user_id=auth.get("user_id"))
    await _usage_check(request, auth, session)

    if request.provider != "buddy_cloud" or not BUDDY_CLOUD_URL:
        raise HTTPException(status_code=400, detail="Streaming solo disponible para Buddy Cloud")

    return await proxy_buddy_cloud_stream(request)


async def proxy_buddy_cloud_stream(request: LLMRequest):
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    headers = {"Content-Type": "application/json"}
    if LLM_SERVICE_API_KEY:
        headers["x-api-key"] = LLM_SERVICE_API_KEY

    async def sse_proxy():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{BUDDY_CLOUD_URL}/stream",
                headers=headers,
                json={
                    "model": request.model or "buddy-llm",
                    "messages": messages,
                    "temperature": request.temperature,
                },
            ) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    yield f"event: error\ndata: {json.dumps({'error': sanitize(error_body.decode())})}\n\n"
                    return
                async for line in resp.aiter_lines():
                    if line:
                        yield line + "\n"

    return StreamingResponse(
        sse_proxy(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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

    headers = {"Content-Type": "application/json"}
    if LLM_SERVICE_API_KEY:
        headers["x-api-key"] = LLM_SERVICE_API_KEY

    async with httpx.AsyncClient(timeout=None) as client:
        res = await client.post(
            BUDDY_CLOUD_URL,
            headers=headers,
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
