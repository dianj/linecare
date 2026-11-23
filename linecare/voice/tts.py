"""Text-to-speech behind one interface: edge-tts (default, online, retried) and a fake for tests."""
import asyncio
import logging
from typing import Protocol

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
    if kind == "fake":
        return FakeTTS()
    raise ValueError(f"Unknown TTS_PROVIDER '{kind}' (use edge or fake)")
