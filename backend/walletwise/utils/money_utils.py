import re

from .text_utils import clean_text


def clean_money_parts(parts) -> str:
    if not parts:
        return ""

    value = "".join(clean_text(part) for part in parts).replace(" ", "")
    value = re.sub(r"(?i)c+r$", "Cr", value)
    value = re.sub(r"(?i)d+r$", "Dr", value)
    return value


def looks_like_money(value) -> bool:
    value = clean_text(value).replace(" ", "")
    if not value:
        return False

    return bool(
        re.fullmatch(
            r"(?i)R?-?\(?\d[\d,]*(?:\.\d{1,2})?\)?(?:Cr|Dr)?",
            value,
        )
    )


def parse_money(value):
    text = clean_text(value).replace(" ", "").replace(",", "")
    text = re.sub(r"^[Rr]", "", text)

    suffix = None
    if text.lower().endswith("cr"):
        suffix = "cr"
        text = text[:-2]
    elif text.lower().endswith("dr"):
        suffix = "dr"
        text = text[:-2]

    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None

    return float(text), suffix


def signed_amount(value):
    parsed = parse_money(value)
    if parsed is None:
        return None

    number, suffix = parsed
    if suffix == "cr":
        return abs(number)
    return -abs(number)


def signed_balance(value):
    parsed = parse_money(value)
    if parsed is None:
        return None

    number, suffix = parsed
    if suffix == "cr":
        return abs(number)
    if suffix == "dr":
        return -abs(number)

    return None
