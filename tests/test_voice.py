"""Voice with fake Whisper and edge-tts objects: no model download, no network."""
import asyncio
import io
import json
from asyncio import sleep as real_sleep  # kept before the backoff is patched out
from concurrent.futures import ThreadPoolExecutor

import edge_tts
import faster_whisper
import pytest
from fastapi.testclient import TestClient

import server
from linecare import config, db
from linecare.voice import stt, tts
from linecare.voice.speech_text import clean_for_speech, normalise_account_numbers
from mocks.scenarios import DEMO_BY_ACCOUNT

MARIA = DEMO_BY_ACCOUNT["QN-100003"]


def run_async(coroutine):
    """asyncio.run in its own thread: the Playwright tests leave an event loop running in the main thread."""
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


@pytest.mark.parametrize("heard, expected", [
    ("My account is Q N one zero zero zero zero three.", "My account is QN-100003."),
    ("q.n. 1 0 0 0 0 3 thanks", "QN-100003 thanks"),
    ("QN 100 003", "QN-100003"),
    ("Account QN100003", "Account QN-100003"),
    ("Q-N, one, zero, zero, zero, oh, three", "QN-100003"),
    ("кю ен едно нула нула нула нула три, ПИН едно пет", "QN-100003, ПИН едно пет"),
    ("Кю-ен 100003", "QN-100003"),
    ("номерът е ку ен 100 три нула нула", "номерът е QN-100300"),
])
def test_spoken_account_numbers_are_normalised(heard, expected):
    assert normalise_account_numbers(heard) == expected


@pytest.mark.parametrize("heard", ["Q N one two three", "QNB box", "the queen is here", "Quinn 100003",
                                   "QN one zero zero zero zero zero seven", "QN-100003 is mine"])
def test_other_text_is_left_alone(heard):
    assert normalise_account_numbers(heard) == heard


def test_replies_are_prepared_for_speech():
    text = "**Case 00001001** for QN-100003:\n- forget the network\n1. restart [guide](https://x.io)"
    assert clean_for_speech(text) == "Case 0 0 0 0, 1 0 0 1 for Q N, 1 0 0, 0 0 3: forget the network restart guide"
    assert len(clean_for_speech("word " * 1000)) <= config.MAX_TTS_CHARS


class FakeWhisper:
    def __init__(self, probs=None, fail=False, text=" Q N one zero zero zero zero three | no internet "):
        self.probs, self.fail, self.text, self.kwargs = probs or {}, fail, text, None

    def detect_language(self, audio, **kwargs):
        if self.fail:
            raise RuntimeError("no speech")
        return "xx", 1.0, list(self.probs.items())

    def transcribe(self, audio, language, **kwargs):
        self.kwargs = {"language": language, **kwargs}
        return [type("Segment", (), {"text": part})() for part in self.text.split("|")], None


def test_only_english_and_bulgarian_are_compared():
    assert stt.pick_supported_language(FakeWhisper({"ru": 0.6, "mk": 0.2, "bg": 0.15, "en": 0.05}), "A") == "bg"
    assert stt.pick_supported_language(FakeWhisper(fail=True), "A") == "en"


def test_transcription_normalises_the_account_number(monkeypatch):
    model = FakeWhisper({"en": 0.9})
    monkeypatch.setattr(stt, "get_whisper_model", lambda: model)
    monkeypatch.setattr(faster_whisper, "decode_audio", lambda path: "AUDIO")
    assert stt.transcribe_audio("clip.webm", "auto") == ("QN-100003 no internet", "en")
    assert "Quillnet" in model.kwargs["initial_prompt"] and model.kwargs["vad_filter"] is True
    stt.transcribe_audio("clip.webm", "bg")
    assert model.kwargs["language"] == "bg" and model.kwargs["initial_prompt"] == config.WHISPER_PROMPTS["bg"]


def test_whisper_loads_once_on_the_cpu(monkeypatch):
    created = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *a, **k: created.append(k) or object())
    monkeypatch.setattr(stt, "_whisper_model", None)
    assert stt.get_whisper_model() is stt.get_whisper_model()
    assert created == [{"device": "cpu", "compute_type": "int8"}]


class FakeCommunicate:
    """edge_tts.Communicate stand-in: fails `failures` times, then streams two audio chunks."""
    failures = 0
    voices = []

    def __init__(self, text, voice):
        FakeCommunicate.voices.append(voice)

    async def stream(self):
        if FakeCommunicate.failures:
            FakeCommunicate.failures -= 1
            raise RuntimeError("No audio was received")
        yield {"type": "WordBoundary"}
        yield {"type": "audio", "data": b"ID3"}
        yield {"type": "audio", "data": b"abc"}


@pytest.fixture
def fake_edge(monkeypatch):
    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))
    FakeCommunicate.failures, FakeCommunicate.voices = 0, []
    return FakeCommunicate


def test_edge_tts_picks_the_voice_and_retries(fake_edge):
    fake_edge.failures = 2
    audio, problem = run_async(tts.EdgeTTS().synthesize("Здравейте", "bg", "male"))
    assert audio == b"ID3abc" and problem is None
    assert fake_edge.voices == ["bg-BG-BorislavNeural"] * 3
    fake_edge.failures = config.TTS_ATTEMPTS
    audio, problem = run_async(tts.EdgeTTS().synthesize("Hi", "xx", "robot"))  # unknown: defaults
    assert audio is None and "No audio was received" in problem and fake_edge.voices[-1] == "en-US-AvaNeural"


def test_tts_provider_selection():
    assert isinstance(tts.make_tts("edge"), tts.EdgeTTS) and isinstance(tts.make_tts("fake"), tts.FakeTTS)
    with pytest.raises(ValueError):
        tts.make_tts("elevenlabs")  # comes in D4


# --- HTTP --------------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(server.app)


def post_audio(client, data=b"x" * 2000, language="auto"):
    return client.post("/api/customer/voice", files={"audio": ("speech.webm", io.BytesIO(data), "audio/webm")},
                       data={"language": language})


def test_a_voice_turn_is_transcribed_redacted_and_marked_as_voice(client, fake_lm, monkeypatch):
    heard = f"кю ен едно нула нула нула нула три, ПИН {MARIA.pin}, нямам интернет"
    monkeypatch.setattr(server, "transcribe_audio", lambda path, language: (normalise_account_numbers(heard), "bg"))
    fake_lm.script_rounds([("verify_customer", {"account_no": "QN-100003", "pin": MARIA.pin})], reply="Здравейте!")
    data = post_audio(client).json()
    assert data["user_text"].startswith("QN-100003") and data["spoken_language"] == "bg"
    assert data["state"]["identity"] == "verified" and data["reply"] == "Здравейте!"
    assert "channel: voice" in fake_lm.requests[0]["messages"][0]["content"]
    log = db.conversation_log(data["state"]["conversation"])
    assert log["conversation"]["channel"] == "voice" and log["turns"][0]["stt_ms"] is not None
    assert MARIA.pin not in json.dumps(log, ensure_ascii=False)


def test_unclear_too_long_and_failed_recordings(client, monkeypatch):
    monkeypatch.setattr(server, "transcribe_audio", lambda path, language: ("", "bg"))
    assert post_audio(client).json()["reply"] == server.NOT_UNDERSTOOD["bg"]
    assert post_audio(client, data=b"x" * (config.MAX_AUDIO_BYTES + 1)).status_code == 413

    def broken(path, language):
        raise RuntimeError("decoder")

    monkeypatch.setattr(server, "transcribe_audio", broken)
    assert post_audio(client).json()["error_code"] == "stt_failed"


def test_text_to_speech_endpoint(client, monkeypatch):
    fake = tts.FakeTTS()
    monkeypatch.setattr(server, "tts_provider", fake)
    response = client.post("/api/tts", data={"text": "**Case 00001001**", "language": "bg", "gender": "male"})
    assert response.status_code == 200 and response.headers["content-type"] == "audio/mpeg"
    assert fake.calls == [("Case 0 0 0 0, 1 0 0 1", "bg", "bg-BG-BorislavNeural")]
    assert client.post("/api/tts", data={"text": "**"}).status_code == 400

    class Down:
        async def synthesize(self, text, language, gender):
            return None, "offline"

    monkeypatch.setattr(server, "tts_provider", Down())
    assert client.post("/api/tts", data={"text": "Hi"}).json()["error_code"] == "tts_failed"
