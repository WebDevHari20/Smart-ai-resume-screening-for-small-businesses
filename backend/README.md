# AI-Based Smart Resume Screening & Candidate Recommendation: Backend

FastAPI backend implementing the full pipeline: **upload/OCR → NLP parsing → semantic JD matching →
GitHub + LeetCode proof-of-work verification → explainable composite scoring → recruiter feedback loop.**

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set JWT_SECRET and GITHUB_TOKEN
uvicorn app.main:app --reload   # docs at http://localhost:8000/docs
pytest                          # 8 tests
```
Optional upgrades (auto-detected, graceful fallback if missing): `sentence-transformers` (SBERT; else TF-IDF),
`spacy` + `en_core_web_sm` (better name extraction), `pytesseract` + `pdf2image` (OCR for scanned PDFs).
Production: `docker compose up` (PostgreSQL + all optional deps).

## Pipeline → code map
| Flowchart stage | File | What it does |
|---|---|---|
| Resume upload & OCR | `services/extraction.py` | PDF (pdfplumber) / DOCX / TXT, hyperlink annotations, Tesseract OCR fallback, hidden-text detection |
| NLP preprocessing | `services/nlp.py`, `skills.py` | Section segmentation, name/email/phone, skills taxonomy, experience (date-range merge), education, GitHub/LeetCode link extraction |
| Semantic JD matching | `services/matching.py` | SBERT chunk-level cosine (TF-IDF fallback), computed on **blinded** text |
| Proof-of-work check | `services/proof_of_work.py` | GitHub API + LeetCode GraphQL → verifies repos, activity, and each claimed skill |
| Composite scoring | `services/scoring.py` | Weighted, renormalised, fully explainable; integrity-flag penalties |
| Dashboard API | `routers/candidates.py` | Ranked list, blind mode, detail + explanation, CSV export |
| Feedback loop | `routers/jobs.py` → `/weights/learn` | Logistic regression on shortlist/reject decisions suggests new weights |
| Data layer | `models.py`, `database.py` | SQLAlchemy; SQLite default, PostgreSQL via `DATABASE_URL` |

## Endpoints (all except `/auth/*` and `/health` need `Authorization: Bearer <token>`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register`, `/auth/login` | Recruiter accounts (JWT) |
| POST/GET | `/jobs`, `/jobs/{id}` | Create job (skills auto-extracted from JD if omitted) |
| PUT | `/jobs/{id}/weights` | Set weights; all candidates rescored |
| POST | `/jobs/{id}/weights/learn?apply=false` | Learn weights from recruiter decisions (suggest-only by default) |
| POST | `/jobs/{id}/resumes` | Upload up to 25 PDF/DOCX/TXT files (multipart `files`) |
| GET | `/jobs/{id}/candidates?min_score=&status=&blind=` | Ranked shortlist |
| GET | `/candidates/{id}` | Full explanation: components, contributions, verified/unverified claims, flags |
| GET | `/candidates/{id}/resume.pdf` | Display the original uploaded PDF when available; legacy/non-PDF uploads use a text fallback |
| POST | `/candidates/{id}/feedback` | `shortlist` / `reject` / `interview` / `note` |
| POST | `/candidates/{id}/confirmation-link` | Generate a secure confirmation link for a shortlisted candidate |
| POST | `/candidates/{id}/reverify` | Re-run GitHub/LeetCode checks |
| DELETE | `/candidates/{id}` | Erase resume + derived data (privacy) |
| GET | `/jobs/{id}/export.csv` | Export ranking |
| GET/POST | `/whatsapp/confirm/{token}` | Candidate views and accepts/declines a one-time confirmation request |

Uploaded PDF bytes are retained so the recruiter resume viewer preserves the original formatting, layout, and graphics. Candidates uploaded before this storage was introduced cannot have their original file reconstructed and therefore use the existing extracted-text PDF fallback. DOCX and TXT uploads are still parsed for screening; their original files are not converted to PDF by this endpoint.

### Free confirmation-link workflow

The free workflow does not call WhatsApp or any messaging provider. Shortlisting never sends a
message automatically. A recruiter explicitly generates a single-use, expiring confirmation link,
copies it, and shares it manually through WhatsApp, SMS, or email. Candidate responses are tracked
by the app. Set `WHATSAPP_PUBLIC_URL` to a public HTTPS URL when sharing outside the local machine;
if it is empty, the API returns a relative local path for development.

For demonstrations, the frontend opens WhatsApp with a prefilled message to one of the configured
demo numbers and the recruiter must press Send. These demo numbers are not stored in the database,
and newly uploaded resume phone numbers are discarded rather than persisted. Replace the demo
numbers before any real use.

## Scoring
`score = Σ wᵢ·componentᵢ × integrity_multiplier` over skills / experience / education / semantic / proof-of-work
(defaults 0.30 / 0.15 / 0.10 / 0.25 / 0.20). If proof-of-work is **not applicable or couldn't be verified**
(API down, non-technical job), it is excluded and the other weights renormalise: candidates are never silently
penalised for an outage. If a technical job's resume has **no** verifiable links, proof-of-work scores 0 and is flagged.

Proof-of-work = GitHub (45%: commits in 90d, portfolio, recency, community, linked-repo verification)
+ LeetCode (20%: difficulty-weighted solves + contest rating) + claim verification (35%: claimed skills found in the
candidate's **own non-fork** repos; DSA claims backed by solve counts). Linked repos must be owned by, or list the
candidate as a contributor, with ≥1 authored commit.

## Responsible-AI features (from the 2025–26 literature)
- **Prompt-injection defence**: detects white/tiny/hidden PDF text, zero-width chars and instruction-like phrases → score ×0.5 and mandatory manual review. Scoring is deterministic and never feeds resume text to an LLM.
- **Blind screening**: semantic matching runs on text stripped of name, contact, gender/age/marital cues; `blind=true` hides identity in API responses.
- **Keyword-stuffing** detection (density / repeat counts) → ×0.8.
- **Explainability**: every score returns per-component contributions, matched/missing skills, verified/unverified claims and plain-English reasons.
- **Human in the loop**: no auto-reject; learned weights are suggestions until `apply=true`.

## Known limitations (be upfront about these in your report)
- LeetCode has **no official public API**; the GraphQL endpoint is unofficial and may change or block requests. Failures degrade gracefully (flagged, not penalised).
- GitHub unauthenticated limit is 60 req/hr: set `GITHUB_TOKEN`.
- Skill detection is taxonomy-based (extend `services/skills.py`); synonyms outside it are missed.
- Verifying public profiles should be disclosed to candidates; add a consent notice in the UI.
- Uploads are processed synchronously per request (fine for small-business volumes); move to a task queue (Celery/RQ) to scale.
