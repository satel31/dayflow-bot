from __future__ import annotations

from datetime import datetime

from dayflow.celery_app import celery_app, settings


@celery_app.task(name="dayflow.smoke")
def smoke() -> dict[str, str]:
    return {
        "status": "ok",
        "timezone": settings.timezone,
        "checked_at": datetime.now().astimezone().isoformat(),
    }
