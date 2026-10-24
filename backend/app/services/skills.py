"""Skill taxonomy: canonical name -> aliases. Used for extraction, JD parsing and GitHub evidence matching."""
import re

SKILLS: dict[str, list[str]] = {
    # languages
    "python": ["python", "python3"], "java": ["java"], "javascript": ["javascript", "js", "es6"],
    "typescript": ["typescript"], "c++": ["c++", "cpp"], "c#": ["c#", "csharp"], "go": ["golang"],
    "rust": ["rust"], "php": ["php"], "kotlin": ["kotlin"], "swift": ["swift"],
    # web / backend
    "react": ["react", "react.js", "reactjs"], "node.js": ["node.js", "nodejs", "node js"],
    "django": ["django"], "flask": ["flask"], "fastapi": ["fastapi"], "spring boot": ["spring boot", "springboot"],
    "html": ["html", "html5"], "css": ["css", "css3"], "tailwind": ["tailwind", "tailwindcss"],
    "rest api": ["rest api", "restful", "rest apis"], "graphql": ["graphql"],
    # data / ml
    "sql": ["sql"], "postgresql": ["postgresql", "postgres"], "mysql": ["mysql"], "mongodb": ["mongodb", "mongo"],
    "sqlite": ["sqlite"], "pandas": ["pandas"], "numpy": ["numpy"], "scikit-learn": ["scikit-learn", "sklearn"],
    "tensorflow": ["tensorflow"], "pytorch": ["pytorch"], "machine learning": ["machine learning", "ml"],
    "deep learning": ["deep learning"], "nlp": ["nlp", "natural language processing"],
    "data analysis": ["data analysis", "data analytics"],
    # devops / cloud
    "docker": ["docker"], "kubernetes": ["kubernetes", "k8s"], "aws": ["aws", "amazon web services"],
    "azure": ["azure"], "gcp": ["gcp", "google cloud"], "git": ["git", "github", "gitlab"],
    "linux": ["linux"], "ci/cd": ["ci/cd", "cicd", "github actions", "jenkins"],
    # cs fundamentals
    "data structures": ["data structures"], "algorithms": ["algorithms", "dsa", "competitive programming"],
    # business tools & marketing (small-business roles)
    "excel": ["excel", "ms excel", "microsoft excel"], "power bi": ["power bi", "powerbi"], "tableau": ["tableau"],
    "seo": ["seo", "search engine optimization"], "digital marketing": ["digital marketing"],
    "content strategy": ["content strategy", "content marketing"],
    "google analytics": ["google analytics", "ga4"], "paid campaigns": ["paid campaigns", "google ads", "ppc", "meta ads"],
    "social media marketing": ["social media marketing", "social media"], "email marketing": ["email marketing"],
    "copywriting": ["copywriting"], "project management": ["project management"], "agile": ["agile", "scrum"],
    "accounting": ["accounting", "bookkeeping"], "customer support": ["customer support", "customer service"],
    "sales": ["sales", "b2b sales"], "communication": ["communication"], "leadership": ["leadership"],
}

# Skills we can verify against GitHub repos (language/topics/name/description evidence).
CODE_VERIFIABLE = {
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust", "php", "kotlin", "swift",
    "react", "node.js", "django", "flask", "fastapi", "spring boot", "html", "css", "tailwind", "graphql",
    "sql", "postgresql", "mysql", "mongodb", "sqlite", "pandas", "numpy", "scikit-learn", "tensorflow",
    "pytorch", "machine learning", "deep learning", "nlp", "docker", "kubernetes", "aws", "azure", "gcp",
    "ci/cd", "rest api",
}
# Skills verified through LeetCode solve counts instead.
DSA_SKILLS = {"data structures", "algorithms"}

# Extra tokens used only when matching GitHub repo metadata (too ambiguous for resume text).
EVIDENCE_EXTRA: dict[str, list[str]] = {"go": ["go"], "javascript": ["node", "npm"], "sql": ["database", "sqlalchemy"]}

_ALIAS_TO_CANON: dict[str, str] = {}
for canon, aliases in SKILLS.items():
    _ALIAS_TO_CANON[canon] = canon
    for a in aliases:
        _ALIAS_TO_CANON[a.lower()] = canon

_ALIASES_SORTED = sorted(_ALIAS_TO_CANON, key=len, reverse=True)
_SKILL_RE = re.compile(
    r"(?<![A-Za-z0-9+#.])(" + "|".join(re.escape(a) for a in _ALIASES_SORTED) + r")(?![A-Za-z0-9+#])",
    re.IGNORECASE,
)


def canonical(skill: str) -> str:
    s = skill.strip().lower()
    return _ALIAS_TO_CANON.get(s, s)


def extract_skills(text: str) -> tuple[list[str], dict[str, int]]:
    """Return (sorted unique canonical skills, mention counts)."""
    counts: dict[str, int] = {}
    for m in _SKILL_RE.finditer(text):
        c = _ALIAS_TO_CANON[m.group(1).lower()]
        counts[c] = counts.get(c, 0) + 1
    return sorted(counts), counts


def evidence_tokens(skill: str) -> list[str]:
    toks = {skill, *[a.lower() for a in SKILLS.get(skill, [])], *EVIDENCE_EXTRA.get(skill, [])}
    return sorted(toks)
