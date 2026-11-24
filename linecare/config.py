"""Settings from environment variables (see .env.example), read at import time. Imported by every other module."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_PATH = BASE_DIR / "index.html"

# --- LLM (LM Studio, OpenAI-compatible API) ---
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1235/v1")
LMSTUDIO_ROOT = LLM_BASE_URL.rstrip("/").removesuffix("/v1")
# Models offered in the UI as (LM Studio model key, display label). The first one is the default.
# Only one fits in GPU memory, so switching unloads the other one (see lmstudio.py).
ALLOWED_MODELS = [
    ("gemma-4-26b-a4b-it", "Gemma 4 26B"),
    ("qwen3.8-27b", "Qwen 3.8 27B"),
]
MODEL_NAME = os.environ.get("LLM_MODEL", ALLOWED_MODELS[0][0])
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))
# "none" skips the model's hidden reasoning (measured in ai-comment-moderator: ~12x faster, same accuracy).
# Empty = the model's default.
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "none")
LOAD_CONTEXT_LENGTH = int(os.environ.get("LLM_CONTEXT_LENGTH", "16384"))
MODEL_LOAD_TIMEOUT = 600
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "8"))
MAX_HISTORY_TURNS = 16  # older user turns are dropped (whole turns, so tool-call pairs stay complete)

# --- Confirmation gate (docs/DECISIONS.md): a calibrated check that the customer said yes before an action runs ---
DECISION_GATE = os.environ.get("DECISION_GATE", "1") == "1"
CALIBRATION_DIR = BASE_DIR / os.environ.get("CALIBRATION_DIR", "evals/decisions/calibration")
DECISION_TIMEOUT_SECONDS = float(os.environ.get("DECISION_TIMEOUT_SECONDS", "20"))

# --- Storage ---
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "data" / "linecare.db"))

# --- Staff (helpdesk agent, operator console) ---
# Demo authentication: token -> role. Override with STAFF_TOKENS="token1:operator,token2:technician".
STAFF_TOKENS = dict(item.split(":", 1) for item in os.environ.get(
    "STAFF_TOKENS", "demo-operator:operator,demo-technician:technician").split(",") if ":" in item)

# --- CRM (see linecare/crm/) ---
CRM_BACKEND = os.environ.get("CRM_BACKEND", "fake")  # fake (SQLite) | salesforce
# Salesforce: an External Client App with the client-credentials flow (README, "Salesforce setup").
SF_LOGIN_URL = os.environ.get("SF_LOGIN_URL", "")  # the org's My Domain, e.g. https://example.my.salesforce.com
SF_CLIENT_ID = os.environ.get("SF_CLIENT_ID", "")
SF_CLIENT_SECRET = os.environ.get("SF_CLIENT_SECRET", "")
SF_API_VERSION = os.environ.get("SF_API_VERSION", "")  # e.g. v66.0; empty = the newest the org offers
SF_TIMEOUT_SECONDS = float(os.environ.get("SF_TIMEOUT_SECONDS", "20"))

# --- Knowledge base (see linecare/kb/) ---
KB_DIR = BASE_DIR / "kb"
CHROMA_PATH = Path(os.environ.get("CHROMA_PATH", BASE_DIR / "data" / "chroma"))
EMBEDDER = os.environ.get("EMBEDDER", "onnx")  # onnx | fake
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBED_CACHE_DIR = Path(os.environ.get("EMBED_CACHE_DIR", BASE_DIR / "data" / "fastembed"))
# Relevance threshold for `answerable`. Empty = the value calibrated for the embedding model (kb/embedder.py).
KB_MIN_SCORE = float(os.environ["KB_MIN_SCORE"]) if os.environ.get("KB_MIN_SCORE") else None
# 1 = also index kb/test (the poisoned PUB-99 used by injection tests); never in the demo index.
KB_INCLUDE_TEST_DOCS = os.environ.get("KB_INCLUDE_TEST_DOCS", "0") == "1"
KB_TOP_K = 4

# --- Voice (see linecare/voice/) ---
# Speech-to-text runs locally on the CPU; the GPU is kept for the chat model.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
MAX_AUDIO_BYTES = 15 * 1024 * 1024
VOICE_LANGUAGES = ("en", "bg")
WHISPER_PROMPTS = {
    "en": "Quillnet support: account number QN-100042, router, QN-Box 4, QN-Mesh Duo, Wi-Fi, PPPoE, outage, "
          "technician, bill.",
    "bg": "Поддръжка на Quillnet: номер на акаунт QN-100042, рутер, QN-Box 4, QN-Mesh Duo, Wi-Fi, авария, техник, "
          "сметка, интернет.",
}
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "edge")  # edge | elevenlabs | fake
# edge-tts uses Microsoft's online neural voices, so reply text leaves the machine.
TTS_VOICES = {
    "female": {"en": "en-US-AvaNeural", "bg": "bg-BG-KalinaNeural"},
    "male": {"en": "en-US-AndrewNeural", "bg": "bg-BG-BorislavNeural"},
}
DEFAULT_VOICE_GENDER = "female"
MAX_TTS_CHARS = 1200
TTS_ATTEMPTS = 3
TTS_ATTEMPT_TIMEOUT = 15
# ElevenLabs (TTS_PROVIDER=elevenlabs): reply text goes to their API. A key restricted to Text to Speech is enough.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_URL = os.environ.get("ELEVENLABS_URL", "https://api.elevenlabs.io")
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")   # low latency, Bulgarian included
ELEVENLABS_VOICES = {   # premade voices (no voice library needed); override with ELEVENLABS_VOICE_FEMALE / _MALE
    "female": os.environ.get("ELEVENLABS_VOICE_FEMALE", "EXAVITQu4vr4xnSDxMaL"),
    "male": os.environ.get("ELEVENLABS_VOICE_MALE", "nPczCjzI2devNBz1zQrb"),
}

# --- Clock ---
# An ISO datetime freezes "now" for tests, evals and a repeatable demo. Empty = the real clock.
FROZEN_NOW = os.environ.get("FROZEN_NOW", "")

# --- Customer agent rules ---
MAX_IDENTITY_ATTEMPTS = 3
IDENTITY_LOCK_MINUTES = 15
MAX_REBOOTS_PER_CONVERSATION = 2
# A pending confirmation is valid in the next user turns only; after that the customer must be asked again.
CONFIRMATION_TTL_TURNS = 2
MAX_MESSAGE_CHARS = 1000
MAX_SESSIONS = 500
