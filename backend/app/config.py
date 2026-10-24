import os
from dataclasses import dataclass, field


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./screener.db")
    jwt_secret: str = os.getenv("JWT_SECRET", "change-me-in-production")
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    whatsapp_enabled: bool = _bool("WHATSAPP_ENABLED", "false")
    whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_api_version: str = os.getenv("WHATSAPP_API_VERSION", "v21.0")
    whatsapp_public_url: str = os.getenv("WHATSAPP_PUBLIC_URL", "")
    whatsapp_request_expiry_hours: int = int(os.getenv("WHATSAPP_REQUEST_EXPIRY_HOURS", "72"))
    enable_external_verification: bool = _bool("ENABLE_EXTERNAL_VERIFICATION", "true")
    use_sbert: bool = _bool("USE_SBERT", "true")
    sbert_model: str = os.getenv("SBERT_MODEL", "all-MiniLM-L6-v2")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
    max_files_per_request: int = int(os.getenv("MAX_FILES_PER_REQUEST", "25"))
    cors_origins: list = field(
        default_factory=lambda: [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8501").split(",") if o.strip()]
    )


settings = Settings()
