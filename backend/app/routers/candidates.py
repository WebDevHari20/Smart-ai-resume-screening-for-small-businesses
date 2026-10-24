import asyncio
import csv
import io
import random
import textwrap
from datetime import datetime, timezone
from typing import List, Annotated
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from pydantic import WithJsonSchema
from fastapi import UploadFile as DefaultUploadFile
from ..config import settings
from ..database import get_db
from ..models import Candidate, Feedback, User, WhatsAppConfirmation
from ..services.whatsapp import create_token, token_expiry
from ..schemas import FeedbackIn
from ..security import current_user
from ..services import pipeline, proof_of_work
from ..services.extraction import ResumeError
from .jobs import owned_job

router = APIRouter(tags=["candidates"])
STATUS = {"shortlist": "shortlisted", "reject": "rejected", "interview": "interview"}
DEMO_WHATSAPP_NUMBERS = ("917975389968", "916362007694", "919663610206", "919381181220")

UploadFile = Annotated[
    DefaultUploadFile, 
    WithJsonSchema({"type": "string", "format": "binary"})
]

def _mask(email: str) -> str:
    return (email[0] + "***@" + email.split("@")[1]) if "@" in email else ""


def summary(c: Candidate, blind: bool) -> dict:
    s = c.scores
    return {
        "id": c.id, "name": f"Candidate #{c.id}" if blind else (c.name or c.filename), "email": "" if blind else c.email,
        "phone": "" if blind else c.phone,
        "filename": c.filename, "final_score": c.final_score, "tier": s["tier"], "status": c.status,
        "matched_skills": s["matched_skills"], "missing_skills": s["missing_skills"],
        "components": s["components"], "proof_of_work_score": c.proof.get("score"),
        "github": (c.proof.get("github") or {}).get("username") or c.parsed["links"].get("github_username"),
        "leetcode": c.parsed["links"].get("leetcode_username"),
        "proof_evidence": {
            "github_active_days_30d": (c.proof.get("github") or {}).get("active_days_30d"),
            "github_active_days_90d": (c.proof.get("github") or {}).get("active_days_90d"),
            "github_active_days_365d": (c.proof.get("github") or {}).get("active_days_365d"),
            "github_active_repos_12m": (c.proof.get("github") or {}).get("active_repos_12m"),
            "github_commits_90d": (c.proof.get("github") or {}).get("commits_90d"),
            "github_related_claims": c.proof.get("claims", []),
        },
        "whatsapp_confirmation": _whatsapp_summary(c),
        "flags": [{"code": f["code"], "severity": f["severity"], "message": f["message"]} for f in c.flags],
        "needs_manual_review": s["needs_manual_review"],
    }



def _owned_candidate(db: Session, cid: int, user: User) -> Candidate:
    c = db.get(Candidate, cid)
    if not c or c.job.owner_id != user.id:
        raise HTTPException(404, "Candidate not found")
    return c


def _whatsapp_summary(c: Candidate) -> dict | None:
    request = max(c.whatsapp_requests, key=lambda item: item.created_at, default=None)
    if not request:
        return None
    return {"status": request.status, "phone": c.phone, "phone_last4": request.phone_last4,
            "expires_at": request.expires_at, "sent_at": request.sent_at,
            "responded_at": request.responded_at}


def _resume_pdf(text: str) -> bytes:
    lines = []
    for paragraph in (text or "No resume text is available.").splitlines():
        clean = paragraph.encode("latin-1", "replace").decode("latin-1")
        lines.extend(textwrap.wrap(clean, width=94) or [""])
    pages = [lines[index:index + 48] for index in range(0, len(lines), 48)] or [[""]]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(f"{3 + i * 2} 0 R".encode() for i in range(len(pages))) +
        b"] /Count " + str(len(pages)).encode() + b" >>",
    ]
    for index, page_lines in enumerate(pages):
        page_object = 3 + index * 2
        content_object = page_object + 1
        commands = ["BT", "/F1 10 Tf", "50 750 Td", "14 TL"]
        for line in page_lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj")
            commands.append("T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 " + str(3 + len(pages) * 2).encode() + b" 0 R >> >> "
            b"/Contents " + str(content_object).encode() + b" 0 R >>"
        )
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


@router.post("/candidates/{cid}/confirmation-link")
def generate_confirmation_link(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    from ..config import settings

    c = _owned_candidate(db, cid, user)
    if c.status != "shortlisted":
        raise HTTPException(409, "Only shortlisted candidates can receive a confirmation link")
    token, token_hash = create_token()
    demo_phone = c.phone
    if demo_phone not in DEMO_WHATSAPP_NUMBERS:
        demo_phone = random.choice(DEMO_WHATSAPP_NUMBERS)
        c.phone = demo_phone
    request = WhatsAppConfirmation(candidate_id=c.id, token_hash=token_hash, phone_last4=demo_phone[-4:],
                                   expires_at=token_expiry())
    db.add(request)
    request.status = "link_generated"
    db.commit()
    path = f"/whatsapp/confirm/{token}"
    base = settings.whatsapp_public_url.rstrip("/")
    url = f"{base}/{token}" if base else path
    return {"status": request.status, "confirmation_url": url, "confirmation_path": path,
            "demo_phone": demo_phone,
            "expires_at": request.expires_at}


@router.post("/jobs/{job_id}/resumes", status_code=201)
async def upload_resumes(
    job_id: int, 
    files: List[UploadFile] = File(...), 
    db: Session = Depends(get_db), 
    user: User = Depends(current_user)
    ):
    job = owned_job(db, job_id, user)
    if len(files) > settings.max_files_per_request:
        raise HTTPException(400, f"Max {settings.max_files_per_request} files per request")
    ctx = pipeline.job_context(job)
    errors, payloads = [], []
    for f in files:
        content = await f.read()
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            errors.append({"filename": f.filename, "error": f"File exceeds {settings.max_upload_mb}MB"})
        else:
            payloads.append((f.filename or "resume", content, f.content_type or "application/octet-stream"))
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        async def run(name, content, _content_type):
            async with sem:
                return await pipeline.process_resume(name, content, ctx, client)
        results = await asyncio.gather(*[run(n, c, t) for n, c, t in payloads], return_exceptions=True)
    existing = {h for (h,) in db.query(Candidate.content_hash).filter(Candidate.job_id == job.id)}
    created = []
    for (name, content, content_type), res in zip(payloads, results):
        if isinstance(res, ResumeError):
            errors.append({"filename": name, "error": str(res)})
        elif isinstance(res, Exception):
            errors.append({"filename": name, "error": f"Processing failed: {type(res).__name__}"})
        elif res["hash"] in existing:
            errors.append({"filename": name, "error": "Duplicate resume already uploaded for this job"})
        else:
            existing.add(res["hash"])
            p = {**res["parsed"], "phone": ""}
            demo_phone = random.choice(DEMO_WHATSAPP_NUMBERS)
            cand = Candidate(job_id=job.id, filename=name, content_hash=res["hash"], name=p["name"], email=p["email"], phone=demo_phone,
                             original_file=content, original_content_type=content_type,
                             raw_text=res["text"], parsed=p, proof=res["proof"], flags=res["flags"], scores=res["scores"],
                             final_score=res["scores"]["final_score"])
            db.add(cand)
            created.append(cand)
    db.commit()
    created.sort(key=lambda c: -c.final_score)
    return {"processed": [summary(c, False) for c in created], "errors": errors}


@router.get("/jobs/{job_id}/candidates")
def ranked_candidates(job_id: int, min_score: float = 0, status: str | None = None, blind: bool = False,
                      db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Ranked shortlist. blind=true hides names/emails for bias-free first-pass review."""
    owned_job(db, job_id, user)
    q = db.query(Candidate).filter(Candidate.job_id == job_id, Candidate.final_score >= min_score)
    if status:
        q = q.filter(Candidate.status == status)
    return [{"rank": i + 1, **summary(c, blind)} for i, c in enumerate(q.order_by(Candidate.final_score.desc()))]


@router.get("/candidates/{cid}")
def candidate_detail(cid: int, blind: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = _owned_candidate(db, cid, user)
    parsed = {k: v for k, v in c.parsed.items() if k != "skill_mentions"}
    if blind:
        parsed = {**parsed, "name": "", "email": "", "phone": ""}
    return {**summary(c, blind), "parsed": parsed, "explanation": c.scores, "proof_of_work": c.proof,
            "feedback": [{"action": f.action, "note": f.note, "at": f.created_at} for f in c.feedback]}


@router.get("/candidates/{cid}/resume.pdf")
def candidate_resume_pdf(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = _owned_candidate(db, cid, user)
    if c.original_file and (c.original_content_type == "application/pdf" or c.filename.lower().endswith(".pdf")):
        return Response(
            content=c.original_file,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{c.filename}"'},
        )
    return Response(
        content=_resume_pdf(c.raw_text),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="candidate_{c.id}_resume.pdf"'},
    )


@router.post("/candidates/{cid}/feedback")
def feedback(cid: int, body: FeedbackIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Recruiter makes the final call; decisions feed the weight-learning loop."""
    c = _owned_candidate(db, cid, user)
    if body.action in STATUS:
        c.status = STATUS[body.action]
    db.add(Feedback(candidate_id=c.id, action=body.action, note=body.note))
    db.commit()
    return {"id": c.id, "status": c.status}


@router.post("/candidates/{cid}/reverify")
async def reverify(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Re-run GitHub/LeetCode verification (e.g. after a rate-limit failure) and rescore."""
    c = _owned_candidate(db, cid, user)
    ctx = pipeline.job_context(c.job)
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        proof = await proof_of_work.verify_candidate(client, c.parsed, ctx["required_skills"], ctx["requires_proof_of_work"])
    base_flags = [f for f in c.flags if f["code"] not in ("no_verifiable_links", "unverified_project_link")]
    c.flags = base_flags + proof.pop("flags", [])
    c.proof = proof
    res = pipeline.rescore(ctx, c.parsed, c.scores["components"]["semantic"], proof, c.flags)
    c.scores, c.final_score = res, res["final_score"]
    db.commit()
    return summary(c, False)


@router.delete("/candidates/{cid}", status_code=204)
def delete_candidate(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Data-privacy: permanently remove a candidate's resume and derived data."""
    db.delete(_owned_candidate(db, cid, user))
    db.commit()


@router.get("/jobs/{job_id}/export.csv")
def export_csv(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_job(db, job_id, user)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "name", "email", "score", "tier", "status", "matched_skills", "missing_skills", "proof_of_work", "flags"])
    for i, c in enumerate(db.query(Candidate).filter(Candidate.job_id == job_id).order_by(Candidate.final_score.desc()), 1):
        w.writerow([i, c.name, c.email, c.final_score, c.scores["tier"], c.status, ";".join(c.scores["matched_skills"]),
                    ";".join(c.scores["missing_skills"]), c.proof.get("score"), ";".join(f["code"] for f in c.flags)])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=job_{job_id}_candidates.csv"})
