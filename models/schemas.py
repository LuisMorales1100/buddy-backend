from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any

class DisplayConfig(BaseModel):
    width: int = 240
    height: int = 240


class AnimationsConfig(BaseModel):
    active: str = "default"
    installed: List[str] = Field(default_factory=lambda: ["default"])
    activeExpr: str = "idle"


class VoiceConfig(BaseModel):
    language: str = "es-ES"
    record_seconds: int = 4
    tts_enabled: bool = True
    tts_rate: float = 1.1
    tts_pitch: float = 1.2
    wake_word: str = "buddy"


class AgentConfig(BaseModel):
    id: str = "default"
    name: str = "Buddy"
    provider: str = "buddy_cloud"
    model: str = "llama3"
    temperature: float = 0.7
    endpoint: str = "https://api.buddyrobots.com/v1/llm/chat"


class SpotifyConfig(BaseModel):
    enabled: bool = False
    intensity: str = "medium"
    led_sync: bool = True
    motor_sync: bool = False


class WeatherConfig(BaseModel):
    enabled: bool = False
    location: str = "Ciudad de México,MX"
    use_gps: bool = False
    hot_threshold: int = 30
    cold_threshold: int = 15
    unit: str = "celsius"
    check_interval_min: int = 30


class ReactionsConfig(BaseModel):
    enabled: bool = True
    spotify: SpotifyConfig = Field(default_factory=SpotifyConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    priority: List[str] = Field(default_factory=lambda: ["weather", "spotify", "chat"])


class DeviceConfigSchema(BaseModel):
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    animations: AnimationsConfig = Field(default_factory=AnimationsConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    reactions: ReactionsConfig = Field(default_factory=ReactionsConfig)

    class Config:
        extra = "allow"

class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    last_known_ip: Optional[str] = None
    config: Optional[DeviceConfigSchema] = None

class DeviceResponse(BaseModel):
    serial: str
    name: Optional[str] = None
    last_known_ip: Optional[str] = None
    config: dict = {}
    created_at: Optional[str] = None

    class Config:
        from_attributes = True

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
    endpoint: Optional[str] = None
    api_key: Optional[str] = None

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
    title: Optional[str] = None
    messages: Optional[list[ConversationMessage]] = None
    messages_raw: Optional[str] = None

class SyncMessageCreate(BaseModel):
    role: str
    content: str
    audio_url: Optional[str] = None
    audio_duration_ms: Optional[int] = None
    expression: Optional[str] = None

class SyncConversationCreate(BaseModel):
    title: Optional[str] = None
    source: str = 'text'
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