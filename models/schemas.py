from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any

# LLM
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class LLMRequest(BaseModel):
    provider: Literal["openai", "anthropic", "ollama", "custom", "local", "buddy_cloud"]
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

# Conversation sync (voice/text)
class SyncMessageCreate(BaseModel):
    role: str  # 'user' or 'assistant'
    content: str
    audio_url: Optional[str] = None
    audio_duration_ms: Optional[int] = None
    expression: Optional[str] = None

class SyncConversationCreate(BaseModel):
    device_id: str
    title: Optional[str] = None
    source: str = 'text'  # 'voice' or 'text'
    messages: list[SyncMessageCreate]

class SyncMessageResponse(BaseModel):
    id: str
    conversation_id: int
    role: str
    content: str
    audio_url: Optional[str] = None
    audio_duration_ms: Optional[int] = None
    expression: Optional[str] = None
    created_at: str

class SyncConversationResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    device_id: Optional[str] = None
    title: Optional[str] = None
    source: str = 'text'
    status: str = 'active'
    messages: list[SyncMessageResponse] = []
    created_at: str
    updated_at: str
    deleted_at: Optional[str] = None

class ConversationRestoreResponse(BaseModel):
    status: str
    conversation_id: int