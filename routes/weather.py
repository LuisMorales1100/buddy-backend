import os
import httpx
from fastapi import APIRouter, HTTPException
from models.schemas import WeatherRequest, WeatherResponse
from services.cache import cache

router = APIRouter(prefix="/weather", tags=["Weather"])

WEATHER_KEY = os.getenv("OPENWEATHER_API_KEY", "")

@router.post("/", response_model=WeatherResponse)
async def get_weather(request: WeatherRequest):
    cache_key = f"weather_{request.location}_{request.unit}"
    cached = cache.get(cache_key)
    if cached:
        return WeatherResponse(**cached)
    
    if not WEATHER_KEY:
        raise HTTPException(status_code=503, detail="Weather API not configured")
    
    units = "imperial" if request.unit == "fahrenheit" else "metric"
    q = request.location.replace(" ", "%20")
    
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.openweathermap.org/data/2.5/weather",
            params={
                "q": q,
                "units": units,
                "lang": request.lang,
                "appid": WEATHER_KEY
            }
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Weather API error")
        
        data = res.json()
        result = {
            "location": data["name"],
            "country": data["sys"]["country"],
            "temperature": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "humidity": data["main"]["humidity"],
            "description": data["weather"][0]["description"],
            "icon": data["weather"][0]["icon"],
            "unit": "fahrenheit" if units == "imperial" else "celsius"
        }
        
        cache.set(cache_key, result, ttl=600)  # 10 min cache
        return WeatherResponse(**result)