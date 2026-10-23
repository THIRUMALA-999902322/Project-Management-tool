"""Deterministic reading of what people send: stations, quantities, checklist evidence.

Used on its own when no model is configured, and always as a cross-check on the model's
observations (e.g. a quantity that doesn't match the station span).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STATION = r"\d{1,3}\+\d{2}(?:\.\d+)?"
RANGE_RE = re.compile(
    rf"(?:sta(?:tion)?s?\.?\s*)?({STATION})\s*(?:to|-|–|—|through|thru)\s*(?:sta(?:tion)?\.?\s*)?({STATION})", re.I
)
STATION_RE = re.compile(rf"(?<![\d.])({STATION})(?![\d])")
SHEET_RANGE_RE = re.compile(r"\b([A-Z]{1,2})-(\d{1,3})\s*(?:to|-|–|through|thru)\s*(?:[A-Z]{1,2}-)?(\d{1,3})\b")

_NUM = r"(\d[\d,]*(?:\.\d+)?)"
QTY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_NUM + r"\s*(?:lf\b|l\.f\.|linear\s+f(?:ee|oo)t\b|feet\b|foot\b|ft\b|')", re.I), "LF"),
    (re.compile(_NUM + r"\s*(?:sy\b|square\s+yards?\b)", re.I), "SY"),
    (re.compile(_NUM + r"\s*(?:cy\b|cubic\s+yards?\b)", re.I), "CY"),
    (re.compile(_NUM + r"\s*(?:services?|meter\s*box(?:es)?|meters?|reconnect\w*)\b", re.I), "EA"),
    (re.compile(_NUM + r"\s*sheets?\b", re.I), "sheets"),
    (re.compile(_NUM + r"\s*(?:sections?)\b", re.I), "sections"),
    (re.compile(_NUM + r"\s*(?:ea\b|each\b|inlets?\b|manholes?\b|hydrants?\b|valves?\b)", re.I), "EA"),
    (re.compile(_NUM + r"\s*%", re.I), "%"),
]

UNIT_ALIASES = {
    "lf": "LF",
    "ft": "LF",
    "feet": "LF",
    "foot": "LF",
    "linear feet": "LF",
    "ea": "EA",
    "each": "EA",
    "services": "EA",
    "service": "EA",
    "sy": "SY",
    "cy": "CY",
    "sheet": "sheets",
    "sheets": "sheets",
    "section": "sections",
    "sections": "sections",
    "%": "%",
    "percent": "%",
    "weeks": "weeks",
    "week": "weeks",
}


def norm_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.strip()
    return UNIT_ALIASES.get(u.lower(), u)


def station_value(label: str) -> float:
    whole, rest = label.split("+", 1)
    return int(whole) * 100 + float(rest)


def station_label(value: float) -> str:
    whole, rest = divmod(round(value, 2), 100)
    rest_s = f"{rest:05.2f}".rstrip("0").rstrip(".") if rest % 1 else f"{int(rest):02d}"
    return f"{int(whole)}+{rest_s}"


def find_station_range(text: str) -> tuple[str, str] | None:
    m = RANGE_RE.search(text)
    if m:
        return m.group(1), m.group(2)
    found = STATION_RE.findall(text)
    if len(found) >= 2:
        return found[0], found[-1]
    return None


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def find_quantity(text: str, job_unit: str | None) -> tuple[float, str] | None:
    """First quantity in `text`, preferring one in the job's own unit."""
    want = norm_unit(job_unit)
    hits: list[tuple[int, float, str]] = []
    for pattern, unit in QTY_PATTERNS:
        for m in pattern.finditer(text):
            # "12+00" style numbers are stations, not quantities.
            if "+" in text[max(0, m.start() - 4) : m.start()]:
                continue
            hits.append((m.start(), _to_float(m.group(1)), unit))
    if want == "sheets":
        for m in SHEET_RANGE_RE.finditer(text):
            hits.append((m.start(), float(int(m.group(3)) - int(m.group(2)) + 1), "sheets"))
    if not hits:
        return None
    hits.sort()
    preferred = [h for h in hits if h[2] == want]
    _, qty, unit = (preferred or hits)[0]
    return qty, unit


def evidence_present(item: str, *, photos: int, docs: list[tuple[str, str]], texts: list[str]) -> bool:
    """Is a checklist item ("Photos", "Pressure test report", "CCTV report") covered by the inputs?

    docs are (filename, extracted text). A text message saying the thing passed also counts,
    because the manager's review shows the message as the evidence.
    """
    low = item.lower()
    if low in {"photo", "photos"}:
        return photos > 0
    keywords = [w for w in re.findall(r"[a-z0-9]+", low) if w not in {"report", "the", "of", "a", "an", "and", "pdf"}]
    if not keywords:
        return False
    for name, body in docs:
        hay = f"{name} {body[:20000]}".lower()
        if all(k in hay for k in keywords):
            return True
    for t in texts:
        hay = t.lower()
        if all(k in hay for k in keywords) and re.search(r"\b(passed|pass|done|complete|attached|signed)\b", hay):
            return True
    return False


@dataclass
class RuleDraft:
    quantity: float | None = None
    unit: str | None = None
    station_from: str | None = None
    station_to: str | None = None
    claims: list[str] = field(default_factory=list)
    summary: str = ""
    missing: list[str] = field(default_factory=list)
    question: str | None = None


def draft_from_inputs(
    *,
    job_name: str,
    job_unit: str,
    job_word: str | None,
    checklist: list[str],
    texts: list[str],
    photos: int,
    voice: int,
    docs: list[tuple[str, str]],
) -> RuleDraft:
    """Read the day's messages and transcripts the way a careful clerk would: only what is stated."""
    joined = "\n".join(t for t in texts if t)
    out = RuleDraft()
    rng = find_station_range(joined)
    if rng:
        out.station_from, out.station_to = rng
    qty = None
    for t in reversed(texts):  # the latest statement wins
        qty = find_quantity(t, job_unit)
        if qty:
            break
    if qty:
        out.quantity, out.unit = qty
    elif rng and norm_unit(job_unit) == "LF":
        span = station_value(rng[1]) - station_value(rng[0])
        if span > 0:
            out.quantity, out.unit = span, "LF"

    unit_word = job_word or out.unit or job_unit
    if out.quantity is not None:
        out.claims.append(f"{out.quantity:,.0f} {unit_word} · {job_name}")
    if rng:
        out.claims.append(f"Sta {rng[0]} to {rng[1]}")
    evidence = []
    if photos:
        evidence.append(f"{photos} photo{'s' if photos != 1 else ''}")
    if voice:
        evidence.append(f"{voice} voice note{'s' if voice != 1 else ''}")
    if docs:
        evidence.append(f"{len(docs)} file{'s' if len(docs) != 1 else ''}")
    if evidence:
        out.claims.append(", ".join(evidence))

    out.missing = [item for item in checklist if not evidence_present(item, photos=photos, docs=docs, texts=texts)]
    if out.quantity is None:
        out.question = "How much did you get done today?"
    elif norm_unit(job_unit) == "LF" and not rng:
        out.question = "Which stations?"
    parts = []
    if out.quantity is not None:
        parts.append(f"{out.quantity:,.0f} {unit_word}")
    if rng:
        parts.append(f"Sta {rng[0]} to {rng[1]}")
    out.summary = " · ".join(parts) or "Nothing measurable yet"
    return out
