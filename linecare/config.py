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

# --- Storage ---
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "data" / "linecare.db"))

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
