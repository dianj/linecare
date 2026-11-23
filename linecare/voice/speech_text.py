"""Text rules around speech: spoken account numbers in transcripts, and replies prepared for a voice."""
import re

from linecare import config

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9",
    "нула": "0", "едно": "1", "един": "1", "една": "1", "две": "2", "два": "2", "три": "3", "четири": "4",
    "пет": "5", "шест": "6", "седем": "7", "осем": "8", "девет": "9",
}
# "QN", "Q N", "Q-N", "q.n." and the Bulgarian spellings Whisper writes for the letters: "кю ен", "ку-ен".
PREFIX = re.compile(r"(?<![\w])(?:q[\s.\-]*n|кю[\s\-]*ен|ку[\s\-]*ен)\.?(?![^\W\d_])", re.I)  # digits may follow
TOKEN = re.compile(r"[\s,.\-:]*(\d+|[a-zа-я]+)", re.I)


def normalise_account_numbers(text: str) -> str:
    """'Q N one zero zero zero zero three' / 'кю ен едно нула нула нула нула три' / 'QN 100 003' -> 'QN-100003'.

    Only a prefix followed by exactly six spoken or written digits is replaced; anything else stays as it was.
    """
    out, position = [], 0
    for match in PREFIX.finditer(text):
        if match.start() < position:
            continue
        digits, end = "", match.end()
        while len(digits) < 6:
            token = TOKEN.match(text, end)
            if not token:
                break
            word = token.group(1).lower()
            value = word if word.isdigit() else DIGIT_WORDS.get(word)
            if value is None or len(digits) + len(value) > 6:
                break
            digits, end = digits + value, token.end()
        following = TOKEN.match(text, end)
        if following and (following.group(1).isdigit() or following.group(1).lower() in DIGIT_WORDS):
            continue  # more than six digits: misheard, so leave it for the model to ask again
        if len(digits) == 6:
            out.append(text[position:match.start()] + f"QN-{digits}")
            position = end
    return "".join(out) + text[position:]


ACCOUNT_NO = re.compile(r"\bQN-(\d{3})(\d{3})\b")
CASE_REF = re.compile(r"\b(\d{4}) ?(\d{4})\b")


def _spaced(digits: str) -> str:
    return " ".join(digits)


def clean_for_speech(text: str) -> str:
    """Markdown out; account and case numbers in short groups, so the voice reads them digit by digit."""
    text = ACCOUNT_NO.sub(lambda m: f"Q N, {_spaced(m.group(1))}, {_spaced(m.group(2))}", text)
    text = CASE_REF.sub(lambda m: f"{_spaced(m.group(1))}, {_spaced(m.group(2))}", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|>|[-*•]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*`_#]", "", text)
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()[:config.MAX_TTS_CHARS]
