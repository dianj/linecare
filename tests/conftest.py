"""Shared fixtures: a fake LM Studio, a fresh seeded database per test, a frozen clock and the real app.

Environment variables must be set before `server` (and the `linecare` package) is imported, because they are
read at import time.
"""
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_lmstudio import FakeLMStudio  # noqa: E402

_TMP_DIR = tempfile.mkdtemp(prefix="linecare-tests-")
_FAKE = FakeLMStudio().start()
FROZEN_NOW = "2026-11-20T10:00:00"  # a Friday
os.environ["DB_PATH"] = os.path.join(_TMP_DIR, "linecare.db")
os.environ["LLM_BASE_URL"] = _FAKE.base_url
os.environ["FROZEN_NOW"] = FROZEN_NOW
os.environ["EMBEDDER"] = "fake"  # hashing embedder: tests the wiring and the filters, not retrieval quality
os.environ["CHROMA_PATH"] = os.path.join(_TMP_DIR, "chroma")
os.environ["KB_INCLUDE_TEST_DOCS"] = "0"
os.environ["DECISION_GATE"] = "0"  # the confirmation gate has its own tests (tests/test_confirmation_gate.py)
os.environ["STAFF_TOKENS"] = "demo-operator:operator,demo-technician:technician"   # fixed for the tests only

from linecare import config, db  # noqa: E402  (must come after the environment is prepared)
from mocks.scenarios import DEMO_BY_ACCOUNT  # noqa: E402

db.ensure_database()  # seeding takes a few seconds, so it runs once and each test gets a copy

_TEMPLATE_DB = os.path.join(_TMP_DIR, "template.db")
shutil.copyfile(os.environ["DB_PATH"], _TEMPLATE_DB)


@pytest.fixture(scope="session")
def fake_lm():
    yield _FAKE
    _FAKE.stop()


@pytest.fixture(autouse=True)
def clean_state(fake_lm, monkeypatch):
    """Every test: fresh seeded data, frozen clock, empty sessions, Gemma active and an empty script."""
    fake_lm.reset()
    fake_lm.loaded = {"gemma-4-26b-a4b-it"}
    monkeypatch.setattr(config, "FROZEN_NOW", FROZEN_NOW)
    shutil.copyfile(_TEMPLATE_DB, os.environ["DB_PATH"])
    if "server" in sys.modules:  # the app is imported only by the tests that need it
        from linecare import lmstudio

        lmstudio.model_state.update(active=config.MODEL_NAME, switching=False, target=None, error=None)
        sys.modules["server"].sessions.clear()
        sys.modules["server"].staff_sessions.clear()
        for limiter in sys.modules["server"].limiters.values():   # each test starts with full request buckets
            limiter.reset()
    yield


@pytest.fixture
def new_session():
    from linecare.session import Session

    def make():
        return Session(conv_id=db.new_conversation("customer", config.MODEL_NAME))
    return make


@pytest.fixture
def verified(new_session):
    """A session already verified for a demo customer: verified("QN-100003")."""
    from linecare.tools.identity import verify_customer

    def make(account_no):
        session = new_session()
        session.begin_turn()
        result = verify_customer(session, account_no, pin=DEMO_BY_ACCOUNT[account_no].pin)
        assert result["verified"] is True
        return session
    return make


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def app_url(fake_lm):
    """Run the real FastAPI app (uvicorn) in a background thread."""
    import importlib

    import uvicorn

    app = importlib.import_module("server").app
    port = _free_port()
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not uv.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    uv.should_exit = True
    thread.join(timeout=5)


def pytest_collection_modifyitems(items):
    """Browser tests last: Playwright's sync API leaves an event loop running in the main thread, after which
    asyncio.run() (the confirmation gate's tests) would fail."""
    items.sort(key=lambda item: "e2e" in item.nodeid.split("::")[0])
