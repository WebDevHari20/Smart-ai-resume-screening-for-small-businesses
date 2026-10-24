"""Orchestrates the full flow: extract -> parse -> safety -> semantic match -> proof-of-work -> score."""
import asyncio
import hashlib

import httpx

from . import extraction, nlp, proof_of_work, safety, scoring
from .matching import matcher


def analyze_sync(filename: str, content: bytes, job_ctx: dict) -> dict:
    """CPU-bound stages (run in a worker thread)."""
    ex = extraction.extract_text(filename, content)
    parsed = nlp.parse_resume(ex["text"], ex["links"])
    parsed["used_ocr"] = ex["used_ocr"]
    flags = safety.scan_resume(ex["text"], parsed["skill_mentions"], ex["hidden_chars"], parsed["word_count"])
    blinded = safety.blind_text(ex["text"], parsed["name"])
    semantic = matcher.similarity(blinded, job_ctx["description"])
    return {"text": ex["text"], "parsed": parsed, "flags": flags, "semantic": semantic,
            "hash": hashlib.sha256(ex["text"].encode()).hexdigest()}


async def process_resume(filename: str, content: bytes, job_ctx: dict, client: httpx.AsyncClient) -> dict:
    a = await asyncio.to_thread(analyze_sync, filename, content, job_ctx)
    proof = await proof_of_work.verify_candidate(client, a["parsed"], job_ctx["required_skills"], job_ctx["requires_proof_of_work"])
    flags = a["flags"] + proof.pop("flags", [])
    result = scoring.score_candidate(job_ctx, a["parsed"], a["semantic"], proof, flags)
    return {**a, "proof": proof, "flags": flags, "scores": result}


def rescore(job_ctx: dict, parsed: dict, semantic: float, proof: dict, flags: list[dict]) -> dict:
    return scoring.score_candidate(job_ctx, parsed, semantic, proof, flags)


def job_context(job) -> dict:
    return {"description": job.description, "required_skills": job.required_skills or [], "min_experience": job.min_experience or 0,
            "education_level": job.education_level, "requires_proof_of_work": job.requires_proof_of_work, "weights": job.weights}
