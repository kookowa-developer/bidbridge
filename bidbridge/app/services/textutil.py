from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_APOSTROPHES = str.maketrans({"ʻ": "'", "ʼ": "'", "’": "'", "‘": "'", "`": "'", "´": "'", "ʹ": "'"})

MONTHS: dict[str, int] = {
    # Uzbek (Latin)
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6,
    "iyul": 7, "avgust": 8, "sentabr": 9, "sentyabr": 9, "oktabr": 10, "oktyabr": 10,
    "noyabr": 11, "dekabr": 12,
    # Russian
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_STOPWORDS = frozenset(
    "bilan uchun yoki kamida ko'pi bo'lishi lozim kerak talab etiladi bo'yicha ushbu "
    "this that with from have must shall should least more less than".split()
)

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_DATE_NUM = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_UZ = re.compile(rf"\b(\d{{4}})[-\s]*yil\s+(\d{{1,2}})[-\s]*({_MONTH_ALT})\b", re.IGNORECASE)
_DATE_DMY = re.compile(rf"\b(\d{{1,2}})(?:-)?\s*({_MONTH_ALT})\s+(\d{{4}})\b", re.IGNORECASE)
_DATE_MDY = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE)


def normalize(text: str) -> str:
    """Lowercase, unify Uzbek apostrophes and collapse whitespace."""
    return re.sub(r"\s+", " ", text.translate(_APOSTROPHES)).strip().lower()


def find_dates(text: str) -> list[date]:
    """Return all calendar dates found in ``text`` in order of appearance."""
    found: list[tuple[int, date]] = []
    t = text.translate(_APOSTROPHES)

    def add(pos: int, y: int, m: int, d: int) -> None:
        try:
            found.append((pos, date(y, m, d)))
        except ValueError:
            pass

    for m in _DATE_NUM.finditer(t):
        add(m.start(), int(m.group(3)), int(m.group(2)), int(m.group(1)))
    for m in _DATE_ISO.finditer(t):
        add(m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in _DATE_UZ.finditer(t):
        add(m.start(), int(m.group(1)), MONTHS[m.group(3).lower()], int(m.group(2)))
    for m in _DATE_DMY.finditer(t):
        add(m.start(), int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    for m in _DATE_MDY.finditer(t):
        add(m.start(), int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    seen: set[date] = set()
    out: list[date] = []
    for _, d in sorted(found, key=lambda x: x[0]):
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


@dataclass(frozen=True)
class Amount:
    value: float
    currency: str  # UZS | USD | EUR | BHM | ""


_NUM = r"\d{1,3}(?:[  ,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_MULT = r"(?:(mln|million|миллион\w*|млн)|(mlrd|billion|миллиард\w*|млрд)|(ming|тыс\w*|thousand))"
_CUR = (
    r"(so'?m|sum|uzs|сум\w*|usd|us\$|\$|aqsh\s+dollar\w*|dollar\w*|долл\w*|eur|euro|евро|"
    r"bhm|bcu|базов\w+\s+велич\w*)"
)
_AMOUNT = re.compile(
    rf"(?:(?P<pre>usd|uzs|eur|\$)\s*)?(?P<num>{_NUM})(?:\s*(?P<mult>{_MULT}))?(?:\s*(?P<cur>{_CUR}))?",
    re.IGNORECASE,
)


def _to_float(raw: str) -> float:
    s = raw.replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:[ ,.]\d{3})+", s):
        return float(re.sub(r"[ ,.]", "", s))
    s = s.replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    return float(s)


def _currency(token: str | None) -> str:
    if not token:
        return ""
    t = token.lower().replace(" ", "")
    if t.startswith(("so", "sum", "uzs", "сум")):
        return "UZS"
    if t.startswith(("usd", "us$", "$", "aqsh", "dollar", "долл")):
        return "USD"
    if t.startswith(("eur", "euro", "евро")):
        return "EUR"
    if t.startswith(("bhm", "bcu", "базов")):
        return "BHM"
    return ""


def find_amounts(text: str) -> list[Amount]:
    """Extract monetary amounts that carry an explicit currency marker."""
    out: list[Amount] = []
    for m in _AMOUNT.finditer(text.translate(_APOSTROPHES)):
        cur = _currency(m.group("cur")) or _currency(m.group("pre"))
        if not cur:
            continue
        try:
            value = _to_float(m.group("num"))
        except ValueError:
            continue
        mult = (m.group("mult") or "").lower()
        if mult.startswith(("mln", "million", "миллион", "млн")):
            value *= 1_000_000
        elif mult.startswith(("mlrd", "billion", "миллиард", "млрд")):
            value *= 1_000_000_000
        elif mult.startswith(("ming", "тыс", "thousand")):
            value *= 1_000
        out.append(Amount(value=value, currency=cur))
    return out


def tokens(text: str) -> set[str]:
    """Stemmed (5-char prefix) content tokens; robust to Uzbek agglutinative suffixes."""
    words = re.findall(r"[a-zЀ-ӿ']{4,}", normalize(text))
    return {w[:5] for w in words if w not in _STOPWORDS}


def overlap(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))
