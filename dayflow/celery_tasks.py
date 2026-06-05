from __future__ import annotations

from datetime import datetime

from dayflow.celery_app import celery_app, settings
from dayflow.digest_delivery import send_daily_digest_to_subscribers
from dayflow.digest_service import DigestKind, build_daily_digest_for_user


@celery_app.task(name="dayflow.smoke")
def smoke() -> dict[str, str]:
    return {
        "status": "ok",
        "timezone": settings.timezone,
        "checked_at": datetime.now().astimezone().isoformat(),
    }


@celery_app.task(name="dayflow.build_daily_digest")
def build_daily_digest_task(user_id: int, kind: DigestKind) -> dict[str, str]:
    digest = build_daily_digest_for_user(settings, user_id=user_id, kind=kind)
    return {
        "kind": digest.kind,
        "target_date": digest.target_date.isoformat(),
        "text": digest.text,
    }


@celery_app.task(name="dayflow.send_daily_digest")
def send_daily_digest_task(kind: DigestKind) -> dict:
    result = send_daily_digest_to_subscribers(settings, kind)
    return {
        "kind": result.kind,
        "attempted": result.attempted,
        "sent": result.sent,
        "failed": result.failed,
        "errors": list(result.errors),
    }
