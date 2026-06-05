from __future__ import annotations

from pathlib import Path

from celery import Celery
from celery.schedules import crontab

from dayflow.config import load_settings


settings = load_settings()

broker_transport_options = {}
if settings.celery_broker_url == "filesystem://":
    broker_dir = Path(settings.celery_broker_data_dir)
    queue_dir = broker_dir / "queue"
    processed_dir = broker_dir / "processed"
    queue_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    broker_transport_options = {
        "data_folder_in": str(queue_dir),
        "data_folder_out": str(queue_dir),
        "data_folder_processed": str(processed_dir),
    }

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
    beat_schedule={
        "send-morning-digest": {
            "task": "dayflow.send_daily_digest",
            "schedule": crontab(minute=0, hour=settings.digest_morning_hour),
            "args": ("morning",),
        },
        "send-evening-digest": {
            "task": "dayflow.send_daily_digest",
            "schedule": crontab(minute=0, hour=settings.digest_evening_hour),
            "args": ("evening",),
        },
    },
    broker_transport_options=broker_transport_options,
    task_serializer="json",
    accept_content=("json",),
    result_serializer="json",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)

import dayflow.celery_tasks  # noqa: E402,F401
