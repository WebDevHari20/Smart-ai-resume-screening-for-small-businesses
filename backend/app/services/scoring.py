"""Stage 4: composite, explainable scoring + recruiter-feedback weight learning."""
from .nlp import EDU_RANK

COMPONENTS = ["skills", "experience", "education", "semantic", "proof_of_work"]
DEFAULT_WEIGHTS = {"skills": 0.30, "experience": 0.15, "education": 0.10, "semantic": 0.25, "proof_of_work": 0.20}


def normalize_weights(w: dict | None) -> dict:
    w = {k: max(float((w or {}).get(k, DEFAULT_WEIGHTS[k])), 0.0) for k in COMPONENTS}
    total = sum(w.values()) or 1.0
    return {k: round(v / total, 4) for k, v in w.items()}


def tier(score: float) -> str:
    return "strong_fit" if score >= 75 else "moderate_fit" if score >= 50 else "low_fit"


def score_candidate(job: dict, parsed: dict, semantic: float, proof: dict, flags: list[dict]) -> dict:
    weights = normalize_weights(job.get("weights"))
    required = job.get("required_skills") or []
    cand = set(parsed["skills"])
    matched = [s for s in required if s in cand]
    missing = [s for s in required if s not in cand]
    comps: dict[str, float | None] = {
        "skills": (len(matched) / len(required)) if required else semantic,
        "experience": min(parsed["experience_years"] / job["min_experience"], 1.0) if job.get("min_experience", 0) > 0 else 1.0,
        "education": _edu(job.get("education_level", "none"), parsed["education"]["rank"]),
        "semantic": semantic,
        "proof_of_work": proof.get("score"),
    }
    active = {k: v for k, v in comps.items() if v is not None}
    wsum = sum(weights[k] for k in active) or 1.0
    contrib = {k: round(weights[k] / wsum * v * 100, 2) for k, v in active.items()}
    multiplier = min([f.get("multiplier", 1.0) for f in flags] or [1.0])
    final = round(sum(contrib.values()) * multiplier, 2)
    return {
        "final_score": final, "tier": tier(final), "components": {k: (None if v is None else round(v, 3)) for k, v in comps.items()},
        "contributions": contrib, "weights_used": {k: round(weights[k] / wsum, 3) for k in active},
        "matched_skills": matched, "missing_skills": missing, "penalty_multiplier": multiplier,
        "needs_manual_review": any(f["severity"] == "high" for f in flags),
        "reasons": _reasons(job, parsed, comps, matched, missing, proof, flags, multiplier),
    }


def _edu(required: str, actual_rank: int) -> float:
    need = EDU_RANK.get(required, 0)
    return 1.0 if need == 0 or actual_rank >= need else 0.6 if actual_rank == need - 1 else 0.3


def _reasons(job, parsed, comps, matched, missing, proof, flags, mult) -> list[str]:
    r = []
    if job.get("required_skills"):
        r.append(f"Matches {len(matched)}/{len(job['required_skills'])} required skills" + (f"; missing: {', '.join(missing)}" if missing else ""))
    if job.get("min_experience", 0) > 0:
        r.append(f"{parsed['experience_years']} yrs experience vs {job['min_experience']} required")
    r.append(f"Semantic fit to the job description: {round(comps['semantic'] * 100)}%")
    ver = [c["skill"] for c in proof.get("claims", []) if c["verdict"] == "verified"]
    unv = [c["skill"] for c in proof.get("claims", []) if c["verdict"] == "unverified"]
    if ver:
        r.append(f"Skills verified by real work: {', '.join(ver)}")
    if unv:
        r.append(f"Claimed but no evidence found: {', '.join(unv)}")
    r += proof.get("notes", []) + [f["message"] for f in flags]
    if mult < 1:
        r.append(f"Score reduced x{mult} due to integrity flags")
    return r


def learn_weights(rows: list[tuple[dict, str]], current: dict | None) -> dict | None:
    """rows: (components dict, status). Fit logistic regression on shortlist(1) vs reject(0); blend 50/50 with current weights."""
    X, y = [], []
    for comps, status in rows:
        if status in ("shortlisted", "interview", "rejected"):
            X.append([comps.get(k) if comps.get(k) is not None else 0.5 for k in COMPONENTS])
            y.append(0 if status == "rejected" else 1)
    if len(y) < 10 or len(set(y)) < 2:
        return None
    from sklearn.linear_model import LogisticRegression

    coef = LogisticRegression(C=1.0, max_iter=500).fit(X, y).coef_[0].clip(min=0)
    if coef.sum() == 0:
        return None
    learned = {k: float(c / coef.sum()) for k, c in zip(COMPONENTS, coef)}
    cur = normalize_weights(current)
    return normalize_weights({k: 0.5 * cur[k] + 0.5 * learned[k] for k in COMPONENTS})
