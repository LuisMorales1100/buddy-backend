import os
import base64
import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from models.schemas import SpotifyTokenRequest, SpotifyRefreshRequest
from services.cache import cache

router = APIRouter(prefix="/spotify", tags=["Spotify"])

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
DOMAIN = os.getenv("DOMAIN", "api.buddyrobots.com")
REDIRECT_URI = f"https://{DOMAIN}/v1/spotify/callback"

def get_auth_header():
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    return f"Basic {credentials}"

@router.get("/auth")
async def spotify_auth():
    """Redirige a Spotify OAuth. La app abre esto en popup/WebView."""
    import urllib.parse
    state = os.urandom(8).hex()
    scope = "user-read-currently-playing user-read-playback-state"
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": scope,
        "state": state
    })
    return {"auth_url": f"https://accounts.spotify.com/authorize?{params}"}

@router.get("/callback")
async def spotify_callback(code: str, error: str = None):
    """Callback OAuth. En prod: guardar tokens en DB, devolver session token."""
    if error:
        raise HTTPException(status_code=400, detail=error)
    
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": get_auth_header()},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI
            }
        )
        if res.status_code != 200:
            raise HTTPException(status_code=400, detail="Token exchange failed")
        
        data = res.json()
        # TODO: en producción, guardar en DB asociado al usuario/Buddy
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_in": data["expires_in"]
        }

@router.post("/refresh")
async def spotify_refresh(request: SpotifyRefreshRequest):
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": get_auth_header()},
            data={
                "grant_type": "refresh_token",
                "refresh_token": request.refresh_token
            }
        )
        if res.status_code != 200:
            raise HTTPException(status_code=400, detail="Refresh failed")
        return res.json()

@router.get("/currently-playing")
async def spotify_currently_playing(x_spotify_token: str = Header(...)):
    """Proxy con cache 5 segundos."""
    cache_key = f"spotify_{x_spotify_token[:16]}"  # hash parcial
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {x_spotify_token}"}
        )
        if res.status_code == 204:
            raise HTTPException(status_code=204, detail="No content")
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Spotify API error")
        
        data = res.json()
        cache.set(cache_key, data, ttl=5)
        return data

@router.get("/audio-features/{track_id}")
async def spotify_audio_features(track_id: str, x_spotify_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.spotify.com/v1/audio-features/{track_id}",
            headers={"Authorization": f"Bearer {x_spotify_token}"}
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Spotify API error")
        return res.json()