from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any

# LLM
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class LLMRequest(BaseModel):
    provider: Literal["openai", "anthropic", "ollama", "custom", "local"]
    messages: List[ChatMessage]
    model: Optional[str] = None
    temperature: float = 0.7
    stream: bool = False
    endpoint: Optional[str] = None  # para custom/ollama
    api_key: Optional[str] = None     # para custom (raramente usado)

class LLMResponse(BaseModel):
    text: str
    provider: str
    model: Optional[str] = None
    usage: Optional[dict] = None

# Weather
class WeatherRequest(BaseModel):
    location: str = "Ciudad de México,MX"
    unit: Literal["celsius", "fahrenheit"] = "celsius"
    lang: str = "es"

class WeatherResponse(BaseModel):
    location: str
    country: str
    temperature: float
    feels_like: float
    humidity: int
    description: str
    icon: str
    unit: str

# Spotify
class SpotifyTokenRequest(BaseModel):
    code: str
    redirect_uri: Optional[str] = None

class SpotifyRefreshRequest(BaseModel):
    refresh_token: str

# OTA
class OTACheckResponse(BaseModel):
    current: str
    needs_update: bool
    url: Optional[str] = None
    changelog: Optional[str] = None
    critical: bool = False

# FacePack
class FacePack(BaseModel):
    version: str
    emotions: dict[str, Any]

# Conversations
class ConversationMessage(BaseModel):
    role: str
    content: str
    time: Optional[int] = None

class ConversationCreate(BaseModel):
    title: str = "Nueva conversación"
    messages: list[ConversationMessage] = []

class ConversationUpdate(BaseModel):
    title: str
    messages: list[ConversationMessage]