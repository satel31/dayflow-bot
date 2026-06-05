from __future__ import annotations

from celery import Celery

from dayflow.config import load_settings


settings = load_settings()

celery_app = Celery(
    "dayflow",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=("dayflow.celery_tasks",),
)

celery_app.conf.update(
    timezone=settings.timezone,
    enable_utc=True,
    beat_schedule_filename=settings.celery_beat_schedule_path,
    task_serializer="json",
    accept_content=("json",),
    result_serializer="json",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)

import dayflow.celery_tasks  # noqa: E402,F401
