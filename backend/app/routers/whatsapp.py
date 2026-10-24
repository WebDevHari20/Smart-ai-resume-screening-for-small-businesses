from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Candidate, WhatsAppConfirmation
from ..services.whatsapp import token_is_expired

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


class DecisionIn(BaseModel):
    decision: Literal["accepted", "declined"]


def _request(db: Session, token: str) -> WhatsAppConfirmation:
    import hashlib
    request = db.query(WhatsAppConfirmation).filter(
        WhatsAppConfirmation.token_hash == hashlib.sha256(token.encode()).hexdigest()
    ).first()
    if not request or token_is_expired(request.expires_at):
        raise HTTPException(404, "This confirmation request is invalid or expired")
    return request


@router.get("/confirm/{token}")
def confirmation_details(token: str, db: Session = Depends(get_db)):
    request = _request(db, token)
    candidate = db.get(Candidate, request.candidate_id)
    job = candidate.job
    return {
        "candidate_name": candidate.name or "Candidate",
        "job_title": job.title,
        "job_description": job.description,
        "required_skills": job.required_skills,
        "min_experience": job.min_experience,
        "education_level": job.education_level,
        "status": request.status,
        "expires_at": request.expires_at,
    }


@router.post("/confirm/{token}")
def confirm(token: str, body: DecisionIn, db: Session = Depends(get_db)):
    request = _request(db, token)
    if request.status not in ("pending", "sent", "link_generated"):
        raise HTTPException(409, "This confirmation request has already been decided")
    request.status = body.decision
    request.responded_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": request.status}
