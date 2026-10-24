from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  (register tables)
from .config import settings
from .database import Base, engine
from sqlalchemy import inspect, text
from .routers import auth, candidates, jobs, whatsapp
from .services.matching import matcher


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("candidates")}
    with engine.begin() as connection:
        if "original_file" not in columns:
            connection.execute(text("ALTER TABLE candidates ADD COLUMN original_file " + ("BYTEA" if engine.dialect.name == "postgresql" else "BLOB")))
        if "original_content_type" not in columns:
            connection.execute(text("ALTER TABLE candidates ADD COLUMN original_content_type VARCHAR(100) NOT NULL DEFAULT ''"))
    yield


app = FastAPI(
    title="AI-Based Smart Resume Screening & Candidate Recommendation API",
    description="Resume parsing, semantic matching, GitHub/LeetCode proof-of-work verification and explainable ranking for small businesses.",
    version="1.0.0", lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(candidates.router)
app.include_router(whatsapp.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "semantic_backend": matcher.backend, "external_verification": settings.enable_external_verification,
            "github_token_configured": bool(settings.github_token)}