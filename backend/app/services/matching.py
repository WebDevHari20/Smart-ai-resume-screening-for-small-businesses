"""Stage 3a: semantic resume<->JD matching. SBERT when installed, TF-IDF cosine otherwise."""
import threading

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from ..config import settings


def _chunks(text: str, size: int = 150) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + size]) for i in range(0, max(len(words), 1), size)] or [text]


class SemanticMatcher:
    def __init__(self):
        self._model = None
        self._tried = False
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if not self._tried:
                self._tried = True
                if settings.use_sbert:
                    try:
                        from sentence_transformers import SentenceTransformer

                        self._model = SentenceTransformer(settings.sbert_model)
                    except Exception:
                        self._model = None
        return self._model

    @property
    def backend(self) -> str:
        return "sbert" if self._load() else "tfidf"

    def similarity(self, resume_text: str, jd_text: str) -> float:
        """Return a calibrated 0..1 semantic fit score."""
        if not resume_text.strip() or not jd_text.strip():
            return 0.0
        model = self._load()
        if model is not None:
            embs = model.encode([jd_text] + _chunks(resume_text), normalize_embeddings=True)
            sims = embs[1:] @ embs[0]
            s = float(np.mean(sorted(sims, reverse=True)[:3]))  # best-matching resume chunks
            return float(min(max((s - 0.15) / 0.55, 0.0), 1.0))
        m = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True).fit_transform([jd_text, resume_text])
        cos = float((m[0] @ m[1].T).toarray()[0, 0])
        return float(min(max(cos / 0.45, 0.0), 1.0))


matcher = SemanticMatcher()
