import os
import io
import struct
import structlog
import httpx
import base64
import tempfile
import subprocess
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime
from faster_whisper import WhisperModel
import edge_tts
from pydub import AudioSegment
import miniaudio
import re
import uuid

from models.async_database import get_session, DeviceModel, ConversationModel, ConversationMessageModel

log = structlog.get_logger()
router = APIRouter(prefix="/voice", tags=["Voice"])

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://localhost:3100/v1/llm/chat")
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434/api/chat")
MAX_VOICE_MESSAGES = 50

_whisper_model = None

def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return _whisper_model


class VoiceChatRequest(BaseModel):
    audio: str
    serial: str
    language: str = "es-ES"
    wake_word: Optional[str] = None

class VoiceChatResponse(BaseModel):
    text: str
    audio: str

def _debug_dump_wav(pcm_16k: bytes, tag: str = "debug"):
    """FIX temporal de diagnóstico: vuelca el PCM final (antes de base64) a un
    .wav real para poder escucharlo en la PC y aislar si la distorsión ya viene
    del pipeline TTS (edge_tts + miniaudio) o se introduce después, en el ESP32."""
    wav_bytes = _pcm_to_wav(pcm_16k, 16000, 16, 1)
    path = f"{tag}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.wav"
    with open(path, "wb") as f:
        f.write(wav_bytes)
    log.info("voice.debug_wav_saved", path=path, bytes=len(pcm_16k))

def _iso_to_short(lang: str) -> str:
    return lang.split("-")[0].split("_")[0] if lang else "es"


def _pcm_to_wav(pcm_data: bytes, sample_rate: int, bits_per_sample: int, channels: int) -> bytes:
    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    wav = io.BytesIO()
    wav.write(b'RIFF')
    wav.write(struct.pack('<I', 36 + data_size))
    wav.write(b'WAVE')
    wav.write(b'fmt ')
    wav.write(struct.pack('<I', 16))
    wav.write(struct.pack('<H', 1))
    wav.write(struct.pack('<H', channels))
    wav.write(struct.pack('<I', sample_rate))
    wav.write(struct.pack('<I', byte_rate))
    wav.write(struct.pack('<H', block_align))
    wav.write(struct.pack('<H', bits_per_sample))
    wav.write(b'data')
    wav.write(struct.pack('<I', data_size))
    wav.write(pcm_data)
    return wav.getvalue()


async def _whisper_stt(audio_bytes: bytes, language: str = "es") -> str:
    import io
    wav_bytes = _pcm_to_wav(audio_bytes, 16000, 16, 1)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        tmp_path = f.name
    try:
        import asyncio
        model = _get_whisper_model()
        lang_short = _iso_to_short(language)
        segments, info = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: model.transcribe(
                tmp_path,
                language=lang_short,
                beam_size=5,
                vad_filter=True,
                # FIX: Relajar VAD para audio de ESP32 con posibles micro-pausas.
                # El wake word ya confirma que hay voz; solo evitamos que silencios
                # de < 1s descarten la conversación completa.
                vad_parameters=dict(min_silence_duration_ms=1000, speech_pad_ms=200),
                condition_on_previous_text=False,
            )
        )
        text_parts = []
        for seg in segments:
            text_parts.append(seg.text)
        return " ".join(text_parts).strip()
    finally:
        os.unlink(tmp_path)


async def _call_llm(text: str, language: str = "es") -> str:
    """Llama al LLM service sin inyectar prompts. El LLM service maneja la personalidad."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            res = await client.post(
                LLM_ENDPOINT,
                json={
                    "model": "buddy-llm",
                    "messages": [
                        {"role": "user", "content": text}
                    ],
                    "temperature": 0.7,
                },
            )
            if res.status_code == 200:
                data = res.json()
                result = data.get("response") or data.get("text") or data.get("message", {}).get("content", "")
                if result:
                    return result
        except Exception as e:
            log.warn("llm.buddy_cloud_failed", endpoint=LLM_ENDPOINT, error=str(e))

        # Fallback a Ollama directo
        try:
            res = await client.post(
                OLLAMA_ENDPOINT,
                json={
                    "model": "llama3",
                    "messages": [
                        {"role": "user", "content": text}
                    ],
                    "stream": False,
                    "options": {"temperature": 0.7},
                },
            )
            if res.status_code == 200:
                data = res.json()
                result = data.get("message", {}).get("content", "")
                if result:
                    return result
            else:
                log.error("llm.ollama_http_error", status=res.status_code, body=res.text[:200])
        except Exception as e:
            log.error("llm.ollama_connect_failed", endpoint=OLLAMA_ENDPOINT, error=str(e))

        # Fallback amigable si todo falla
        friendly = {
            "es": "Lo siento, no puedo conectar con mi cerebro ahora mismo. Intenta de nuevo en un momento.",
            "en": "Sorry, I can't connect to my brain right now. Please try again in a moment.",
            "fr": "Désolé, je ne peux pas me connecter à mon cerveau maintenant. Réessayez dans un moment.",
            "de": "Entschuldigung, ich kann mich gerade nicht mit meinem Gehirn verbinden. Versuche es gleich nochmal.",
            "pt": "Desculpe, não consigo conectar ao meu cérebro agora. Tente novamente em um momento.",
            "it": "Scusa, non riesco a connettermi al mio cervello ora. Riprova tra un momento.",
        }
        short = _iso_to_short(language)
        return friendly.get(short, friendly["es"])

async def _edge_tts(text: str, language: str = "es") -> bytes:
    voices = {
        "es": "es-ES-AlvaroNeural",
        "en": "en-US-JennyNeural",
        "fr": "fr-FR-DeniseNeural",
        "de": "de-DE-KatjaNeural",
        "pt": "pt-BR-FranciscaNeural",
        "it": "it-IT-ElsaNeural",
    }
    short = _iso_to_short(language)
    voice = voices.get(short, voices["es"])
    communicate = edge_tts.Communicate(text, voice)
    audio_bytes = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes += chunk["data"]
    return audio_bytes


def _mp3_to_pcm16k(mp3_bytes: bytes) -> bytes:
    """Convierte MP3 a PCM 16-bit 16kHz mono usando miniaudio (sin ffmpeg)."""
    decoded = miniaudio.decode(
        mp3_bytes,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=16000,
    )
    return decoded.samples


async def _save_voice_conversation(session: AsyncSession, serial: str, user_text: str, bot_text: str, audio_b64: str = None, audio_duration_ms: int = None):
    serial_clean = serial.strip().strip('"').strip("'").replace('\x00', '')
    
    result = await session.execute(
        select(DeviceModel).where(DeviceModel.serial == serial_clean)
    )
    device = result.scalar_one_or_none()
    
    if device is None:
        fallback = await session.execute(
            select(DeviceModel).where(DeviceModel.serial.ilike(f"%{serial_clean}%"))
        )
        similar = fallback.scalars().all()
        log.error(
            "voice.device_not_found",
            serial_received=serial,
            serial_clean=serial_clean,
            serial_hex=serial_clean.encode('utf-8').hex(),
            serial_len=len(serial_clean),
            similar_devices=[d.serial for d in similar],
        )
        return None
    
    if not device.user_id:
        log.error(
            "voice.device_no_user",
            serial=serial_clean,
            device_id=device.id,
            device_name=device.name,
            hint="Device exists but has no user_id. Pair device via POST /v1/devices/pair"
        )
        return None
    
    log.info(
        "voice.device_found",
        serial=serial_clean,
        device_id=device.id,
        user_id=device.user_id,
        device_name=device.name
    )
    
    now = datetime.utcnow()
    
    # Buscar conversación activa de voz de ESTE device que NO haya alcanzado el límite.
    result = await session.execute(
        select(ConversationModel).where(
            ConversationModel.origin_device_serial == serial_clean,
            ConversationModel.source == "voice",
            ConversationModel.status == "active",
        ).order_by(ConversationModel.updated_at.desc()).limit(1)
    )
    conv = result.scalar_one_or_none()
    
    # Si existe, contar mensajes. Si al agregar 2 más se excede el límite, crear nueva.
    if conv:
        msg_count_result = await session.execute(
            select(ConversationMessageModel).where(
                ConversationMessageModel.conversation_id == conv.id
            )
        )
        msg_count = len(msg_count_result.scalars().all())
        
        if msg_count + 2 > MAX_VOICE_MESSAGES:
            log.info(
                "voice.conv_limit_reached",
                conversation_id=conv.id,
                msg_count=msg_count,
                max=MAX_VOICE_MESSAGES,
                serial=serial_clean
            )
            conv = None
    
    # Crear nueva conversación si no hay activa o se alcanzó el límite.
    if not conv:
        conv = ConversationModel(
            user_id=device.user_id,
            title="Nueva conversación",
            origin_device_serial=serial_clean,
            linked_device_serials=[serial_clean],
            source="voice",
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(conv)
        await session.flush()
        log.info(
            "voice.new_conversation_created",
            conversation_id=conv.id,
            serial=serial_clean,
            reason="no_active_voice_conv_or_limit_reached"
        )
    
    user_msg = ConversationMessageModel(
        id=str(uuid.uuid4()),
        conversation_id=conv.id,
        role="user",
        content=user_text,
        audio_url=audio_b64 if audio_b64 else None,
        audio_duration_ms=audio_duration_ms if audio_duration_ms else None,
        device_serial=serial_clean,
        created_at=now,
    )
    session.add(user_msg)
    
    bot_msg = ConversationMessageModel(
        id=str(uuid.uuid4()),
        conversation_id=conv.id,
        role="assistant",
        content=bot_text,
        audio_url=audio_b64 if audio_b64 else None,
        audio_duration_ms=audio_duration_ms if audio_duration_ms else None,
        device_serial=serial_clean,
        created_at=now,
    )
    session.add(bot_msg)
    
    conv.updated_at = now
    await session.commit()
    
    log.info(
        "voice.conversation_saved",
        serial=serial_clean,
        conversation_id=conv.id,
        user_id=device.user_id,
        has_audio=bool(audio_b64),
        audio_duration_ms=audio_duration_ms
    )
    return conv.id

@router.post("/chat", response_model=VoiceChatResponse)
async def voice_chat(
    request: VoiceChatRequest,
    session: AsyncSession = Depends(get_session),
):
    serial_clean = request.serial.strip().strip('"').strip("'").replace('\x00', '')
    
    audio_b64_clean = request.audio.strip()
    audio_b64_clean = ''.join(c for c in audio_b64_clean if c in 
        'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
    
    try:
        audio_bytes = base64.b64decode(audio_b64_clean, validate=True)
    except Exception as e:
        log.error("voice.invalid_base64", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid base64 audio")

    if len(audio_bytes) < 32:
        raise HTTPException(status_code=400, detail="Audio too short")

    lang = request.language or "es-ES"
    log.info("voice.chat.start", serial=serial_clean, size=len(audio_bytes), lang=lang, wake_word=request.wake_word)

    transcribed = await _whisper_stt(audio_bytes, lang)
    log.info("voice.stt.done", text=transcribed[:100])

    # FIX: Validación de Wake Word Dinámica
    if request.wake_word:
        ww = request.wake_word.lower().strip()
        transcribed_lower = transcribed.lower().strip()
        
        # FIX: Si no hay transcripción, es "no speech", no "wake word mismatch"
        if not transcribed_lower:
            log.info("voice.no_speech_after_wake", expected=ww)
            return VoiceChatResponse(text="", audio="")
        
        if ww and ww not in transcribed_lower:
            log.info("voice.wake_word_mismatch", expected=ww, got=transcribed_lower)
            return VoiceChatResponse(text="", audio="")
        
        # Extraer comando removiendo wake word
        pattern = re.compile(re.escape(ww), re.IGNORECASE)
        actual_command = pattern.sub('', transcribed, count=1).strip()
        
        if not actual_command:
            log.info("voice.wake_word_only", text=transcribed)
            return VoiceChatResponse(text="", audio="")
            
        transcribed = actual_command

    if not transcribed.strip():
        log.warn("voice.no_speech_detected", serial=serial_clean)
        friendly_retry = {
            "es": "No te escuché bien. ¿Puedes repetirlo, por favor?",
            "en": "I didn't catch that. Could you please repeat?",
        }
        short = _iso_to_short(lang)
        retry_text = friendly_retry.get(short, friendly_retry["es"])
        
        tts_mp3 = await _edge_tts(retry_text, lang)
        pcm_16k = _mp3_to_pcm16k(tts_mp3)
        #_debug_dump_wav(pcm_16k, "retry")  # FIX: temporal, quitar después de diagnosticar
        audio_b64 = base64.b64encode(pcm_16k).decode()
        return VoiceChatResponse(text=retry_text, audio=audio_b64)

    llm_response = await _call_llm(transcribed, lang)
    log.info("voice.llm.done", text=llm_response[:100])

    tts_mp3 = await _edge_tts(llm_response, lang)
    pcm_16k = _mp3_to_pcm16k(tts_mp3)
    #_debug_dump_wav(pcm_16k, "response")  # FIX: temporal, quitar después de diagnosticar
    audio_b64 = base64.b64encode(pcm_16k).decode()

    audio_duration_ms = int((len(pcm_16k) / 32000) * 1000) if pcm_16k else None
    conv_id = await _save_voice_conversation(session, serial_clean, transcribed, llm_response, audio_b64, audio_duration_ms)
    if conv_id is None:
        log.warn("voice.conversation_not_saved_but_audio_returned", serial=serial_clean)
    
    return VoiceChatResponse(text=llm_response, audio=audio_b64)