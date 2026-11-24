"""Text-to-speech behind one interface: edge-tts (default, online, retried), ElevenLabs (online, API key) and a
fake for tests. Both online providers receive the reply text only."""
import asyncio
import logging
from typing import Protocol

import httpx

from linecare import config

log = logging.getLogger("linecare.voice")


class TTSProvider(Protocol):
    async def synthesize(self, text: str, language: str, gender: str) -> tuple[bytes | None, str | None]:
        """Returns (mp3 bytes, None) or (None, error description)."""


def pick_voice(language: str, gender: str) -> str:
    voices = config.TTS_VOICES.get(gender, config.TTS_VOICES[config.DEFAULT_VOICE_GENDER])
    return voices.get(language, voices["en"])


class EdgeTTS:
    async def synthesize(self, text, language, gender):
        import edge_tts

        voice = pick_voice(language, gender)

        async def synthesize() -> bytes:
            audio = bytearray()
            async for chunk in edge_tts.Communicate(text, voice).stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
            return bytes(audio)

        last_error = None
        # The online service sometimes answers "No audio was received", so retry with a short backoff.
        for attempt in range(config.TTS_ATTEMPTS):
            try:
                audio = await asyncio.wait_for(synthesize(), timeout=config.TTS_ATTEMPT_TIMEOUT)
                if audio:
                    return audio, None
                last_error = "empty audio"
            except TimeoutError:
                last_error = f"timeout after {config.TTS_ATTEMPT_TIMEOUT}s"
            except Exception as e:
                last_error = str(e)
            await asyncio.sleep(0.6 * (attempt + 1))
        return None, f"Text-to-speech failed after {config.TTS_ATTEMPTS} attempts: {last_error}"


class ElevenLabsTTS:
    """ElevenLabs text-to-speech over its REST API (plain httpx). One retry on 429, 5xx or a connection error;
    the key is sent as a header and never logged or returned."""

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, api_key: str = None, transport: httpx.AsyncBaseTransport = None):
        self.api_key = api_key if api_key is not None else config.ELEVENLABS_API_KEY
        if not self.api_key:
            raise ValueError("TTS_PROVIDER=elevenlabs needs ELEVENLABS_API_KEY")
        self.transport = transport

    async def synthesize(self, text, language, gender):
        voice = config.ELEVENLABS_VOICES.get(gender, config.ELEVENLABS_VOICES[config.DEFAULT_VOICE_GENDER])
        url = f"{config.ELEVENLABS_URL}/v1/text-to-speech/{voice}"
        body = {"text": text, "model_id": config.ELEVENLABS_MODEL, "language_code": language}
        headers = {"xi-api-key": self.api_key, "Accept": "audio/mpeg"}
        problem = None
        async with httpx.AsyncClient(transport=self.transport, timeout=config.TTS_ATTEMPT_TIMEOUT) as client:
            for attempt in range(2):
                try:
                    response = await client.post(url, params={"output_format": "mp3_44100_128"}, json=body,
                                                 headers=headers)
                except httpx.HTTPError as e:
                    problem = f"ElevenLabs not reachable ({type(e).__name__})"
                else:
                    if response.status_code == 200 and response.content:
                        return response.content, None
                    problem = f"ElevenLabs answered {response.status_code}: {_detail(response)}"
                    if response.status_code not in self.RETRY_STATUS:
                        break
                if attempt == 0:
                    await asyncio.sleep(0.5)
        return None, problem


def _detail(response: httpx.Response) -> str:
    """The error status ElevenLabs reports (e.g. quota_exceeded), without echoing anything else."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        return "no details"
    if isinstance(detail, dict):
        return str(detail.get("status") or detail.get("code") or "error")[:80]
    return str(detail)[:80] if detail else "no details"


class FakeTTS:
    """Records what it was asked to say; returns a tiny fake MP3 frame."""

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, language, gender):
        self.calls.append((text, language, pick_voice(language, gender)))
        return b"ID3fake-mp3", None


def make_tts(kind: str = None) -> TTSProvider:
    kind = kind or config.TTS_PROVIDER
    if kind == "edge":
        return EdgeTTS()
    if kind == "elevenlabs":
        return ElevenLabsTTS()
    if kind == "fake":
        return FakeTTS()
    raise ValueError(f"Unknown TTS_PROVIDER '{kind}' (use edge, elevenlabs or fake)")
