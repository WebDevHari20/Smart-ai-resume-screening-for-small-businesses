import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

import httpx

from ..config import settings


def normalize_phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if value.strip().startswith("+") and 8 <= len(digits) <= 15:
        return digits
    return None


def create_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=settings.whatsapp_request_expiry_hours)


def token_is_expired(expires_at: datetime) -> bool:
    expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc)


async def send_confirmation(phone: str, candidate_name: str, job_title: str, confirmation_url: str) -> str:
    if not settings.whatsapp_enabled or not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise RuntimeError("WhatsApp sending is not configured")
    url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": "candidate_confirmation",
            "language": {"code": "en_US"},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": candidate_name or "Candidate"},
                {"type": "text", "text": job_title},
                {"type": "text", "text": confirmation_url},
            ]}],
        },
    }
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.post(url, json=body, headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"})
    if response.status_code >= 400:
        raise RuntimeError(f"WhatsApp provider rejected the request ({response.status_code})")
    messages = response.json().get("messages") or []
    if not messages or not messages[0].get("id"):
        raise RuntimeError("WhatsApp provider returned no message id")
    return messages[0]["id"]
