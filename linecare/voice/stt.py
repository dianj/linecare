"""Speech-to-text with faster-whisper on the CPU (from voice-restaurant-agent, limited to English and Bulgarian)."""
import logging
import threading

from linecare import config
from linecare.voice.speech_text import normalise_account_numbers

log = logging.getLogger("linecare.voice")
_whisper_model = None
_whisper_lock = threading.Lock()
SAMPLE_RATE = 16000   # what faster-whisper's decode_audio returns


class AudioTooLong(ValueError):
    """A recording longer than MAX_AUDIO_SECONDS (measured after decoding, not by file size)."""

    def __init__(self, seconds: float):
        super().__init__(f"{seconds:.0f} s of audio, more than {config.MAX_AUDIO_SECONDS} s")
        self.seconds = seconds


def get_whisper_model():
    """Load Whisper lazily on the first voice request so the server starts fast."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            _whisper_model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        return _whisper_model


def pick_supported_language(model, audio) -> str:
    """The more likely of English and Bulgarian. Whisper often labels short Bulgarian clips as Russian or
    Macedonian, so only the probabilities of the supported languages are compared."""
    try:
        _, _, all_probs = model.detect_language(audio, vad_filter=True)
        probs = dict(all_probs)
        chosen = max(config.VOICE_LANGUAGES, key=lambda lang: probs.get(lang, 0.0))
        log.info("Speech language: chose %s; %s", chosen,
                 {lang: round(probs.get(lang, 0.0), 3) for lang in config.VOICE_LANGUAGES})
        return chosen
    except Exception:
        log.exception("Language detection failed; using English")
        return "en"


def transcribe_audio(path: str, language: str = None) -> tuple[str, str]:
    """Audio file -> (text, language). A language chosen in the UI skips detection. Spoken account numbers are
    normalised ("Q N one zero ..." -> "QN-100001"). The text is redacted later like typed text."""
    from faster_whisper import decode_audio

    # Decode first and measure: 15 MB of Opus is about an hour of speech, which Whisper on the CPU would grind through.
    audio = decode_audio(path)
    if len(audio) / SAMPLE_RATE > config.MAX_AUDIO_SECONDS:
        raise AudioTooLong(len(audio) / SAMPLE_RATE)
    model = get_whisper_model()
    if language not in config.VOICE_LANGUAGES:
        language = pick_supported_language(model, audio)
    segments, _ = model.transcribe(audio, language=language, vad_filter=True, beam_size=5,
                                   initial_prompt=config.WHISPER_PROMPTS[language])
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return normalise_account_numbers(text), language
