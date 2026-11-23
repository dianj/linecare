"""One clock for the mock systems and the app: honours FROZEN_NOW, so tests and evals are repeatable."""
from datetime import datetime

from linecare import config


def now() -> datetime:
    if config.FROZEN_NOW:
        return datetime.fromisoformat(config.FROZEN_NOW).replace(tzinfo=None)
    return datetime.now().replace(microsecond=0)


def is_frozen() -> bool:
    return bool(config.FROZEN_NOW)
