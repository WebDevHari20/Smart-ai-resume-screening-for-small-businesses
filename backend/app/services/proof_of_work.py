"""Stage 3b: proof-of-work verification via the GitHub REST API and LeetCode's public GraphQL endpoint.

Verifies *claims* on the resume against evidence the candidate cannot edit:
  * GitHub: profile exists, recent commit activity, portfolio, resume-linked repos are real & owned/contributed,
            claimed technologies actually appear in the candidate's own (non-fork) repositories.
  * LeetCode: solved counts by difficulty and contest rating back up DSA claims.
"""
import re
from datetime import datetime, timedelta, timezone

import httpx

from ..config import settings
from .skills import CODE_VERIFIABLE, DSA_SKILLS, evidence_tokens

GH = "https://api.github.com"
LC_GRAPHQL = "https://leetcode.com/graphql"
LC_QUERY = """
query userProfile($username: String!) {
  matchedUser(username: $username) {
    username
    profile { ranking }
    submitStatsGlobal { acSubmissionNum { difficulty count } }
  }
  userContestRanking(username: $username) { rating globalRanking attendedContestsCount topPercentage }
}"""


def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "smart-resume-screener", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        h["Authorization"] = f"Bearer {settings.github_token}"
    return h


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _has_token(haystack: str, token: str) -> bool:
    return re.search(rf"(?<![a-z0-9+#]){re.escape(token)}(?![a-z0-9+#])", haystack) is not None


async def _commit_count(client: httpx.AsyncClient, owner: str, repo: str, author: str) -> int | None:
    r = await client.get(f"{GH}/repos/{owner}/{repo}/commits", params={"per_page": 1, "author": author}, headers=_gh_headers())
    if r.status_code != 200:
        return None
    m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', r.headers.get("link", ""))
    return int(m.group(1)) if m else len(r.json())


async def _verify_repo(client: httpx.AsyncClient, username: str, full: str) -> dict:
    owner, name = full.split("/", 1)
    r = await client.get(f"{GH}/repos/{owner}/{name}", headers=_gh_headers())
    if r.status_code == 404:
        return {"repo": full, "exists": False, "verdict": "not_found"}
    if r.status_code != 200:
        return {"repo": full, "exists": None, "verdict": "unchecked"}
    d = r.json()
    relation = "owner" if owner.lower() == username.lower() else None
    if relation is None:
        c = await client.get(f"{GH}/repos/{owner}/{name}/contributors", params={"per_page": 100}, headers=_gh_headers())
        logins = {x.get("login", "").lower() for x in c.json()} if c.status_code == 200 else set()
        relation = "contributor" if username.lower() in logins else "not_linked"
    commits = await _commit_count(client, owner, name, username)
    verdict = "verified" if relation in ("owner", "contributor") and (commits or 0) > 0 and not d.get("fork") else (
        "forked_only" if d.get("fork") else "unverified")
    return {"repo": full, "exists": True, "relation": relation, "is_fork": d.get("fork", False), "stars": d.get("stargazers_count", 0),
            "primary_language": d.get("language"), "pushed_at": d.get("pushed_at"), "own_commits": commits, "verdict": verdict}


async def fetch_github(client: httpx.AsyncClient, username: str, repo_links: list[str]) -> dict:
    out: dict = {"available": False, "username": username}
    try:
        u = await client.get(f"{GH}/users/{username}", headers=_gh_headers())
        if u.status_code == 404:
            return {**out, "error": "profile_not_found"}
        if u.status_code in (403, 429):
            return {**out, "error": "rate_limited"}
        u.raise_for_status()
        user = u.json()
        rr = await client.get(f"{GH}/users/{username}/repos", params={"per_page": 100, "sort": "pushed"}, headers=_gh_headers())
        repos = rr.json() if rr.status_code == 200 else []
        ev = await client.get(f"{GH}/users/{username}/events/public", params={"per_page": 100}, headers=_gh_headers())
        events = ev.json() if ev.status_code == 200 else []
        now = datetime.now(timezone.utc)
        own = [r for r in repos if not r.get("fork")]
        recent_commits = 0
        active_days = {30: set(), 90: set(), 365: set()}
        for e in events:
            ts = _parse_dt(e.get("created_at"))
            if e.get("type") == "PushEvent" and ts:
                p = e.get("payload", {})
                commits = p.get("distinct_size") or p.get("size") or len(p.get("commits", []))
                age = now - ts
                for days, dates in active_days.items():
                    if age <= timedelta(days=days):
                        dates.add(ts.date().isoformat())
                if age <= timedelta(days=90):
                    recent_commits += commits
        pushed = [d for d in (_parse_dt(r.get("pushed_at")) for r in own) if d]
        last_push = max(pushed) if pushed else None
        active_year = sum(1 for d in pushed if now - d <= timedelta(days=365))
        langs: dict[str, int] = {}
        for r in own:
            if r.get("language"):
                langs[r["language"]] = langs.get(r["language"], 0) + 1
        repo_haystacks = [
            " ".join([r.get("language") or "", r.get("name") or "", r.get("description") or "", *(r.get("topics") or [])]).lower()
            for r in own[:40]
        ]
        verified = [await _verify_repo(client, username, full) for full in repo_links[:5]]
        return {
            "available": True, "username": username, "profile_url": user.get("html_url"),
            "account_age_years": round((now - (_parse_dt(user.get("created_at")) or now)).days / 365, 1),
            "public_repos": user.get("public_repos", 0), "own_repos": len(own), "forks_ignored": len(repos) - len(own),
            "stars": sum(r.get("stargazers_count", 0) for r in own), "forks": sum(r.get("forks_count", 0) for r in own),
            "commits_90d": recent_commits, "active_repos_12m": active_year,
            "active_days_30d": len(active_days[30]), "active_days_90d": len(active_days[90]),
            "active_days_365d": len(active_days[365]),
            "last_push": last_push.isoformat() if last_push else None,
            "top_languages": sorted(langs, key=langs.get, reverse=True)[:6],
            "linked_repos": verified, "_haystacks": repo_haystacks,
        }
    except (httpx.HTTPError, ValueError) as e:
        return {**out, "error": f"request_failed: {type(e).__name__}"}


async def fetch_leetcode(client: httpx.AsyncClient, username: str) -> dict:
    out: dict = {"available": False, "username": username}
    try:
        r = await client.post(
            LC_GRAPHQL, json={"query": LC_QUERY, "variables": {"username": username}},
            headers={"Content-Type": "application/json", "Referer": "https://leetcode.com", "User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200:
            return {**out, "error": f"http_{r.status_code}"}
        data = r.json().get("data") or {}
        mu = data.get("matchedUser")
        if not mu:
            return {**out, "error": "profile_not_found"}
        solved = {x["difficulty"].lower(): x["count"] for x in mu["submitStatsGlobal"]["acSubmissionNum"]}
        contest = data.get("userContestRanking") or {}
        return {
            "available": True, "username": username, "profile_url": f"https://leetcode.com/u/{username}/",
            "total": solved.get("all", 0), "easy": solved.get("easy", 0), "medium": solved.get("medium", 0), "hard": solved.get("hard", 0),
            "ranking": (mu.get("profile") or {}).get("ranking"), "contest_rating": round(contest.get("rating") or 0),
            "contests_attended": contest.get("attendedContestsCount", 0), "top_percentage": contest.get("topPercentage"),
        }
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return {**out, "error": f"request_failed: {type(e).__name__}"}


# ---------- scoring (pure functions, unit-testable) ----------
def github_score(gh: dict) -> float:
    now = datetime.now(timezone.utc)
    last = _parse_dt(gh.get("last_push"))
    recency = 0.0 if not last else 1.0 if now - last <= timedelta(days=90) else 0.5 if now - last <= timedelta(days=365) else 0.0
    linked = gh.get("linked_repos") or []
    verification = (sum(1 for x in linked if x.get("verdict") == "verified") / len(linked)) if linked else 0.5
    active_days = min(gh.get("active_days_90d", 0) / 20, 1)
    return round(
        0.25 * min(gh.get("commits_90d", 0) / 40, 1) + 0.15 * active_days
        + 0.15 * min(gh.get("own_repos", 0) / 10, 1) + 0.15 * recency
        + 0.10 * min((gh.get("stars", 0) + gh.get("forks", 0)) / 30, 1) + 0.20 * verification, 3)


def leetcode_score(lc: dict) -> float:
    weighted = lc.get("easy", 0) + 3 * lc.get("medium", 0) + 5 * lc.get("hard", 0)
    contest = min(max(lc.get("contest_rating", 0) - 1200, 0) / 800, 1)
    return round(0.7 * min(weighted / 600, 1) + 0.3 * contest, 3)


def verify_claims(claimed: list[str], gh: dict | None, lc: dict | None) -> list[dict]:
    results = []
    gh_ok, lc_ok = bool(gh and gh.get("available")), bool(lc and lc.get("available"))
    for skill in claimed:
        if skill in DSA_SKILLS:
            if not lc_ok:
                results.append({"skill": skill, "verdict": "no_data", "evidence": "No LeetCode profile verified"})
                continue
            t = lc["total"]
            v = "verified" if t >= 100 else "partial" if t >= 30 else "unverified"
            results.append({"skill": skill, "verdict": v, "evidence": f"{t} LeetCode problems solved ({lc['hard']} hard)"})
        elif skill in CODE_VERIFIABLE:
            if not gh_ok:
                results.append({"skill": skill, "verdict": "no_data", "evidence": "No GitHub profile verified"})
                continue
            toks = evidence_tokens(skill)
            n = sum(1 for h in gh.get("_haystacks", []) if any(_has_token(h, t) for t in toks))
            v = "verified" if n >= 2 else "partial" if n == 1 else "unverified"
            results.append({"skill": skill, "verdict": v, "evidence": f"Found in {n} of the candidate's own repositories"})
    return results


def build_proof(parsed_links: dict, claimed: list[str], required: list[str], gh: dict | None, lc: dict | None, requires_pow: bool) -> dict:
    notes: list[str] = []
    gh_ok, lc_ok = bool(gh and gh.get("available")), bool(lc and lc.get("available"))
    focus = [s for s in claimed if s in required] or claimed
    claims = verify_claims(focus, gh, lc)
    scored = [c for c in claims if c["verdict"] in ("verified", "partial", "unverified")]
    ratio = (sum(1.0 if c["verdict"] == "verified" else 0.5 if c["verdict"] == "partial" else 0 for c in scored) / len(scored)) if scored else None
    parts: dict[str, tuple[float, float]] = {}
    if gh_ok:
        parts["github"] = (0.45, github_score(gh))
    if lc_ok:
        parts["leetcode"] = (0.20, leetcode_score(lc))
    if ratio is not None:
        parts["claim_verification"] = (0.35, round(ratio, 3))
    has_links = bool(parsed_links.get("github_username") or parsed_links.get("leetcode_username"))
    for label, d in (("GitHub", gh), ("LeetCode", lc)):
        if d and not d.get("available"):
            notes.append(f"{label} profile '{d['username']}' could not be verified ({d.get('error', 'unknown')})")
    flags = []
    if parts:
        wsum = sum(w for w, _ in parts.values())
        score = round(sum(w * s for w, s in parts.values()) / wsum, 3)
    elif not has_links and requires_pow:
        score = 0.0
        notes.append("No GitHub/LeetCode links found on the resume")
        flags.append({"code": "no_verifiable_links", "severity": "medium", "multiplier": 1.0,
                      "message": "Technical role but no verifiable GitHub/LeetCode profile on the resume."})
    else:
        score = None  # not applicable / verification unavailable -> excluded, never silently penalised
        if has_links:
            notes.append("Verification unavailable; proof-of-work excluded from the score pending manual check")
    if gh_ok:
        for r in gh.get("linked_repos", []):
            if r["verdict"] in ("not_found", "forked_only", "unverified"):
                flags.append({"code": "unverified_project_link", "severity": "medium", "multiplier": 1.0,
                              "message": f"Resume-linked repo {r['repo']} could not be verified as the candidate's work ({r['verdict']})."})
    gh_public = {k: v for k, v in (gh or {}).items() if not k.startswith("_")} or None
    return {"score": score, "github": gh_public, "leetcode": lc, "claims": claims,
            "verified_ratio": None if ratio is None else round(ratio, 3), "components": {k: v[1] for k, v in parts.items()},
            "notes": notes, "flags": flags}


async def verify_candidate(client: httpx.AsyncClient, parsed: dict, required: list[str], requires_pow: bool) -> dict:
    links = parsed["links"]
    if not settings.enable_external_verification:
        has = bool(links.get("github_username") or links.get("leetcode_username"))
        return {"score": None if has or not requires_pow else 0.0, "github": None, "leetcode": None, "claims": [],
                "verified_ratio": None, "components": {}, "notes": ["External verification disabled"],
                "flags": [] if has or not requires_pow else [{"code": "no_verifiable_links", "severity": "medium", "multiplier": 1.0,
                                                               "message": "No GitHub/LeetCode links on the resume."}]}
    gh = await fetch_github(client, links["github_username"], links["github_repos"]) if links.get("github_username") else None
    lc = await fetch_leetcode(client, links["leetcode_username"]) if links.get("leetcode_username") else None
    return build_proof(links, parsed["skills"], required, gh, lc, requires_pow)
