"""Stage 2: NLP parsing -> structured candidate profile + proof-of-work link extraction."""
import re
from collections import Counter
from datetime import datetime

from .skills import extract_skills

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{3,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,5}(?!\d)")
GH_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?:/([A-Za-z0-9._-]+))?", re.I)
LC_RE = re.compile(r"(?:https?://)?(?:www\.)?leetcode\.com/(?:u/)?([A-Za-z0-9_-]{2,40})", re.I)
GH_RESERVED = {"features", "about", "orgs", "topics", "settings", "marketplace", "sponsors", "pricing", "login", "explore", "collections"}
LC_RESERVED = {"problems", "contest", "discuss", "explore", "tag", "company", "study-plan", "u", "problemset", "assessment", "subscribe", "accounts"}

HEADERS = {
    "skills": r"(technical |key |core )?skills|core competencies|technologies|tech stack",
    "experience": r"(work |professional |relevant )?experience|employment( history)?|internships?",
    "education": r"education|academic( background| qualifications)?",
    "projects": r"(personal |academic )?projects?",
    "certifications": r"certifications?|courses|licenses",
    "summary": r"summary|objective|profile|about me",
}
_HEADER_RES = {k: re.compile(rf"^(?:{v})$", re.I) for k, v in HEADERS.items()}

MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
RANGE_RE = re.compile(
    rf"(?:{_MON}\s+)?((?:19|20)\d{{2}})\s*(?:-|–|—|to)\s*(?:{_MON}\s+)?((?:19|20)\d{{2}}|present|current|now)", re.I
)
STATED_EXP_RE = re.compile(r"(\d{1,2})\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:[\w/-]+\s+){0,3}?experience", re.I)

EDU_LEVELS = [
    (4, re.compile(r"\b(ph\.?d|doctorate)\b", re.I)),
    (3, re.compile(r"\b(m\.?tech|m\.?sc|master'?s?|mba|mca|m\.?s\.?|m\.?e\.?)\b(?!\w)", re.I)),
    (2, re.compile(r"\b(b\.?tech|b\.?sc|b\.?e\.?|bachelor'?s?|bca|b\.?com|bba|b\.?a\.?)\b(?!\w)", re.I)),
    (1, re.compile(r"\bdiploma\b", re.I)),
]
EDU_NAMES = {0: "none", 1: "diploma", 2: "bachelor", 3: "master", 4: "phd"}
EDU_RANK = {v: k for k, v in EDU_NAMES.items()}

_nlp = None
_nlp_tried = False


def _spacy():
    global _nlp, _nlp_tried
    if not _nlp_tried:
        _nlp_tried = True
        try:
            import spacy

            _nlp = spacy.load("en_core_web_sm", disable=["lemmatizer"])
        except Exception:
            _nlp = None
    return _nlp


def segment_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.splitlines():
        stripped = line.strip().rstrip(":").strip()
        hit = None
        if 2 < len(stripped) < 40:
            for name, rx in _HEADER_RES.items():
                if rx.match(stripped):
                    hit = name
                    break
        if hit:
            current = hit
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def extract_name(text: str) -> str:
    nlp = _spacy()
    head = text[:400]
    if nlp:
        for ent in nlp(head).ents:
            if ent.label_ == "PERSON" and 1 < len(ent.text.split()) <= 4:
                return ent.text.strip()
    for line in text.splitlines()[:6]:
        line = line.strip()
        words = line.split()
        if 2 <= len(words) <= 4 and all(w.replace(".", "").replace("-", "").isalpha() for w in words) and "@" not in line:
            if not any(rx.match(line.rstrip(":")) for rx in _HEADER_RES.values()):
                return line.title() if line.isupper() else line
    return ""


def extract_links(text: str, links: list[str]) -> dict:
    blob = " ".join(links) + " " + text
    gh_users, gh_repos, lc_users = Counter(), set(), Counter()
    for m in GH_RE.finditer(blob):
        user, repo = m.group(1), m.group(2)
        if user.lower() in GH_RESERVED:
            continue
        gh_users[user] += 1
        if repo:
            repo = re.sub(r"\.git$", "", repo).rstrip(".")
            if repo:
                gh_repos.add(f"{user}/{repo}")
    for m in LC_RE.finditer(blob):
        if m.group(1).lower() not in LC_RESERVED:
            lc_users[m.group(1)] += 1
    gh_user = gh_users.most_common(1)[0][0] if gh_users else None
    # a repo link only counts as the candidate's claimed project; ownership is verified later
    return {
        "github_username": gh_user,
        "github_repos": sorted(gh_repos)[:8],
        "leetcode_username": lc_users.most_common(1)[0][0] if lc_users else None,
        "other_links": [l for l in links if not re.search(r"github\.com|leetcode\.com|mailto:", l, re.I)][:10],
    }


def _month_index(year: int, mon: str | None, default_mon: int) -> int:
    return year * 12 + (MONTHS[mon[:3].lower()] if mon else default_mon) - 1


def estimate_experience_years(text: str, sections: dict[str, str]) -> float:
    now = datetime.now()
    source = sections.get("experience") or text.replace(sections.get("education", "\x00"), "")
    spans = []
    for m in RANGE_RE.finditer(source):
        s_mon, s_year, e_mon, e_year = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        start = _month_index(s_year, s_mon, 1)
        if e_year.lower() in ("present", "current", "now"):
            end = now.year * 12 + now.month - 1
        else:
            end = _month_index(int(e_year), e_mon, 12)
        if end >= start and end - start <= 12 * 40:
            spans.append((start, end))
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    computed = sum(e - s + 1 for s, e in merged) / 12
    stated = max([int(x) for x in STATED_EXP_RE.findall(text)] or [0])
    return round(min(max(computed, stated), 40), 1)


def extract_education(text: str, sections: dict[str, str]) -> dict:
    src = sections.get("education") or text
    level = 0
    for rank, rx in EDU_LEVELS:
        if rx.search(src):
            level = max(level, rank)
    lines = [l.strip() for l in (sections.get("education") or "").splitlines() if l.strip()][:6]
    return {"level": EDU_NAMES[level], "rank": level, "details": lines}


def extract_projects(sections: dict[str, str]) -> list[str]:
    lines = [re.sub(r"^[\-\u2022*\d.)\s]+", "", l).strip() for l in sections.get("projects", "").splitlines()]
    return [l for l in lines if 8 < len(l) < 160][:10]


def parse_resume(text: str, links: list[str]) -> dict:
    sections = segment_sections(text)
    skills, counts = extract_skills(text)
    email_m = EMAIL_RE.search(text)
    phone_m = PHONE_RE.search(sections.get("header", text[:500]))
    return {
        "name": extract_name(text),
        "email": email_m.group(0) if email_m else "",
        "phone": phone_m.group(0).strip() if phone_m else "",
        "skills": skills,
        "skill_mentions": counts,
        "experience_years": estimate_experience_years(text, sections),
        "education": extract_education(text, sections),
        "projects": extract_projects(sections),
        "sections_found": [k for k, v in sections.items() if v and k != "header"],
        "links": extract_links(text, links),
        "word_count": len(text.split()),
    }
