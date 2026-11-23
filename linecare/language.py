"""The language of a message: Bulgarian when it is written mostly in Cyrillic, otherwise English."""

NAMES = {"en": "English", "bg": "Bulgarian"}


def detect_language(text: str) -> str:
    letters = [ch for ch in text if ch.isalpha()]
    cyrillic = sum("Ѐ" <= ch <= "ӿ" for ch in letters)
    return "bg" if letters and cyrillic * 3 >= len(letters) else "en"
