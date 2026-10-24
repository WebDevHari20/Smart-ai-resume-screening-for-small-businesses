"""Responsible-AI layer: prompt-injection / hidden-text / keyword-stuffing detection and blind screening."""
import re

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) instructions",
    r"disregard (the )?(above|previous|prior)",
    r"you are (now )?(an? )?(ai|assistant|language model|llm)",
    r"(rank|rate|score|select|shortlist|recommend|hire) (this|me|the) (candidate|resume|applicant)",
    r"(highest|top|perfect|maximum|best) (possible )?(score|rank|rating)",
    r"system prompt",
    r"do not (reject|screen out|filter)",
    r"<\s*/?\s*(system|instructions?)\s*>",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]
_ZW = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_PII = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"https?://\S+|www\.\S+"),
    re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)"),
    re.compile(r"\b(he|she|him|her|his|hers|mr|mrs|ms|miss|male|female|gender|married|single|marital status|"
               r"date of birth|dob|nationality|religion|age[d]?:?\s*\d{2})\b", re.I),
]


def scan_resume(text: str, skill_mentions: dict[str, int], hidden_chars: int, word_count: int) -> list[dict]:
    flags: list[dict] = []
    hits = [p.pattern for p in _INJ if p.search(text)]
    if hits or hidden_chars > 20 or len(_ZW.findall(text)) > 5:
        flags.append({
            "code": "possible_prompt_injection",
            "severity": "high",
            "multiplier": 0.5,
            "message": "Resume contains hidden text or instruction-like phrases aimed at manipulating automated screening. Manual review required.",
            "evidence": {"pattern_hits": len(hits), "hidden_chars": hidden_chars},
        })
    total = sum(skill_mentions.values())
    top = max(skill_mentions.values(), default=0)
    density = total / max(word_count, 1)
    if top > 10 or len(skill_mentions) > 45 or density > 0.25:
        flags.append({
            "code": "keyword_stuffing",
            "severity": "medium",
            "multiplier": 0.8,
            "message": "Unusually dense skill keywords; claims should be verified.",
            "evidence": {"max_mentions": top, "distinct_skills": len(skill_mentions), "density": round(density, 3)},
        })
    return flags


def blind_text(text: str, name: str = "") -> str:
    """Remove identity/demographic signals before semantic matching (bias mitigation)."""
    out = text
    for rx in _PII:
        out = rx.sub(" ", out)
    for part in name.split():
        if len(part) > 2:
            out = re.sub(rf"\b{re.escape(part)}\b", " ", out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip()
