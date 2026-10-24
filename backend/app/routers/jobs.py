from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Candidate, Job, User
from ..schemas import JobIn, WeightsIn
from ..security import current_user
from ..services import pipeline, scoring
from ..services.skills import canonical, extract_skills

router = APIRouter(prefix="/jobs", tags=["jobs"])


def owned_job(db: Session, job_id: int, user: User) -> Job:
    job = db.get(Job, job_id)
    if not job or job.owner_id != user.id:
        raise HTTPException(404, "Job not found")
    return job


def job_out(j: Job) -> dict:
    return {"id": j.id, "title": j.title, "description": j.description, "required_skills": j.required_skills,
            "min_experience": j.min_experience, "education_level": j.education_level,
            "requires_proof_of_work": j.requires_proof_of_work, "weights": scoring.normalize_weights(j.weights),
            "candidate_count": len(j.candidates), "created_at": j.created_at}


@router.post("", status_code=201)
def create_job(body: JobIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    skills = sorted({canonical(s) for s in body.required_skills}) if body.required_skills else extract_skills(body.description)[0]
    job = Job(owner_id=user.id, title=body.title, description=body.description, required_skills=skills,
              min_experience=body.min_experience, education_level=body.education_level,
              requires_proof_of_work=body.requires_proof_of_work)
    db.add(job)
    db.commit()
    return job_out(job)


@router.get("")
def list_jobs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [job_out(j) for j in db.query(Job).filter(Job.owner_id == user.id).order_by(Job.id.desc())]


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return job_out(owned_job(db, job_id, user))


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(owned_job(db, job_id, user))
    db.commit()


@router.delete("/{job_id}/candidates")
def delete_job_candidates(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = owned_job(db, job_id, user)
    candidates = db.query(Candidate).filter(Candidate.job_id == job.id).all()
    for candidate in candidates:
        db.delete(candidate)
    db.commit()
    return {"deleted_count": len(candidates)}


def _rescore_all(db: Session, job: Job) -> int:
    ctx = pipeline.job_context(job)
    for c in job.candidates:
        res = pipeline.rescore(ctx, c.parsed, c.scores["components"]["semantic"], c.proof, c.flags)
        c.scores, c.final_score = res, res["final_score"]
    db.commit()
    return len(job.candidates)


@router.put("/{job_id}/weights")
def set_weights(job_id: int, body: WeightsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = owned_job(db, job_id, user)
    job.weights = scoring.normalize_weights(body.model_dump())
    db.commit()
    return {"weights": job.weights, "rescored": _rescore_all(db, job)}


@router.post("/{job_id}/weights/learn")
def learn_weights(job_id: int, apply: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Learn weights from the recruiter's shortlist/reject decisions. Suggest-only unless apply=true (human stays in control)."""
    job = owned_job(db, job_id, user)
    rows = [(c.scores["components"], c.status) for c in db.query(Candidate).filter(Candidate.job_id == job.id)]
    suggestion = scoring.learn_weights(rows, job.weights)
    if suggestion is None:
        return {"suggested_weights": None, "message": "Need at least 10 decided candidates covering both shortlisted and rejected."}
    out = {"current_weights": scoring.normalize_weights(job.weights), "suggested_weights": suggestion, "applied": apply}
    if apply:
        job.weights = suggestion
        db.commit()
        out["rescored"] = _rescore_all(db, job)
    return out
