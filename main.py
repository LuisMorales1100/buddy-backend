import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routes import llm, spotify, weather, ota, faces, auth, shopify, conversations
from routes.audio_ws import setup_websocket_routes
from services.cache import cache
from models.database import init_db

# Config
PORT = int(os.getenv("PORT", 3001))
DOMAIN = os.getenv("DOMAIN", "api.buddyrobots.com")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🤖 Buddy Backend starting...")
    print(f"📡 Domain: {DOMAIN}")
    init_db()
    yield
    print("👋 Buddy Backend shutting down")


app = FastAPI(title="Buddy Backend API", version="2.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8080",
        "https://buddy.local",
        "https://app.buddyrobots.com",
        "capacitor://localhost",
        "ionic://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rutas API v1
app.include_router(auth.router, prefix="/v1")
app.include_router(llm.router, prefix="/v1")
app.include_router(spotify.router, prefix="/v1")
app.include_router(weather.router, prefix="/v1")
app.include_router(ota.router, prefix="/v1")
app.include_router(faces.router, prefix="/v1")
app.include_router(shopify.router, prefix="/v1")
app.include_router(conversations.router, prefix="/v1")

# WebSocket
setup_websocket_routes(app)


# Health check
@app.get("/v1/health")
async def health():
    return {
        "status": "ok",
        "domain": DOMAIN,
        "version": "2.0.0",
        "cache_size": len(cache._store),
    }


# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
