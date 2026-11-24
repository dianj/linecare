"""The ElevenLabs provider against a fake of its REST API (httpx.MockTransport): request shape, retry, errors."""
import asyncio

import httpx
import pytest

from linecare import config
from linecare.voice.tts import ElevenLabsTTS, make_tts

KEY = "sk_test_key_never_logged"


async def _noop():
    return None


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("linecare.voice.tts.asyncio.sleep", lambda *_: _noop())


def run(provider, text="Hello", language="en", gender="female"):
    return asyncio.run(provider.synthesize(text, language, gender))


def provider_with(*responses):
    """A provider whose API answers with the given (status, body) pairs in order; returns it and the requests."""
    seen, queue = [], list(responses)

    def handle(request):
        seen.append(request)
        status, body = queue.pop(0)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, content=body) if isinstance(body, bytes) else httpx.Response(status, json=body)

    return ElevenLabsTTS(api_key=KEY, transport=httpx.MockTransport(handle)), seen


def test_a_reply_is_sent_with_the_voice_model_and_language():
    provider, seen = provider_with((200, b"ID3mp3"))
    assert run(provider, "Здравейте", "bg", "male") == (b"ID3mp3", None)
    request = seen[0]
    assert request.url.path == f"/v1/text-to-speech/{config.ELEVENLABS_VOICES['male']}"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == KEY
    body = httpx.Response(200, content=request.content).json()
    assert body == {"text": "Здравейте", "model_id": config.ELEVENLABS_MODEL, "language_code": "bg"}


def test_a_rate_limit_or_server_error_is_retried_once():
    provider, seen = provider_with((429, {"detail": {"status": "too_many_concurrent_requests"}}), (200, b"ID3mp3"))
    assert run(provider) == (b"ID3mp3", None) and len(seen) == 2
    provider, seen = provider_with((503, b""), (503, b""))
    audio, problem = run(provider)
    assert audio is None and "503" in problem and len(seen) == 2


def test_a_bad_key_or_quota_is_not_retried_and_the_key_is_never_in_the_error():
    provider, seen = provider_with((401, {"detail": {"status": "invalid_api_key", "message": "Invalid key"}}))
    audio, problem = run(provider)
    assert audio is None and "invalid_api_key" in problem and len(seen) == 1 and KEY not in problem
    provider, _ = provider_with((402, {"detail": {"status": "quota_exceeded"}}))
    assert "quota_exceeded" in run(provider)[1]


def test_a_connection_error_is_retried_then_reported():
    error = httpx.ConnectError("down")
    provider, seen = provider_with((0, error), (0, error))
    audio, problem = run(provider)
    assert audio is None and "not reachable" in problem and len(seen) == 2


def test_elevenlabs_needs_a_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        make_tts("elevenlabs")
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", KEY)
    assert isinstance(make_tts("elevenlabs"), ElevenLabsTTS)
