import re


def clean_text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def clean_description(value) -> str:
    value = clean_text(value)

    # FNB sometimes uses decorative symbols in descriptions.
    value = re.sub(r"^[#•■▪]+\s*", "", value)

    # Tesseract may mistake a vertical table edge for a pipe-like character.
    # Only strip these at the boundaries, never from inside a merchant name.
    value = re.sub(r"^[|¦│]+\s*", "", value)
    value = re.sub(r"\s*[|¦│]+$", "", value)

    return value.strip()


def word_center_x(word) -> float:
    return (word[0] + word[2]) / 2


def word_center_y(word) -> float:
    return (word[1] + word[3]) / 2


def line_text(line) -> str:
    return clean_text(" ".join(word[4] for word in sorted(line, key=lambda w: w[0])))
