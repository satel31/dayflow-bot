from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    timezone: str
    google_calendar_id: str
    google_task_list_id: str
    event_groups_path: str
    google_credentials_path: str
    google_token_path: str
    google_tokens_dir: str
    work_schedule_path: str
    gemini_api_key: str
    gemini_model: str
    outbound_proxy_url: str
    telegram_proxy_url: str
    gemini_proxy_url: str
    workday_start_hour: int
    workday_end_hour: int
    gemini_debug_logging: bool
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_proxy_url: str = ""
    digest_subscribers_path: str = "data/digest_subscribers.json"
    digest_morning_hour: int = 10
    digest_evening_hour: int = 22
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_broker_data_dir: str = "data/celery_broker"
    celery_beat_schedule_path: str = "data/celerybeat-schedule"
    webhook_base_url: str = ""
    telegram_webhook_secret: str = ""
    persistent_backend: str = "file"
    supabase_url: str = ""
    supabase_service_role_key: str = ""


def load_settings() -> Settings:
    outbound_proxy_url = os.getenv("OUTBOUND_PROXY_URL", "").strip()
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        timezone=os.getenv("TIMEZONE", "Europe/Moscow").strip(),
        google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary").strip(),
        google_task_list_id=os.getenv("GOOGLE_TASK_LIST_ID", "@default").strip(),
        event_groups_path=os.getenv(
            "EVENT_GROUPS_PATH", "data/event_groups.json"
        ).strip(),
        google_credentials_path=os.getenv(
            "GOOGLE_CREDENTIALS_PATH", "credentials.json"
        ).strip(),
        google_token_path=os.getenv("GOOGLE_TOKEN_PATH", "token.json").strip(),
        google_tokens_dir=os.getenv("GOOGLE_TOKENS_DIR", "data/google_tokens").strip(),
        digest_subscribers_path=os.getenv(
            "DIGEST_SUBSCRIBERS_PATH", "data/digest_subscribers.json"
        ).strip(),
        work_schedule_path=os.getenv("WORK_SCHEDULE_PATH", "data/work_schedule.json").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        outbound_proxy_url=outbound_proxy_url,
        telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL", outbound_proxy_url).strip(),
        gemini_proxy_url=os.getenv("GEMINI_PROXY_URL", outbound_proxy_url).strip(),
        workday_start_hour=int(os.getenv("WORKDAY_START_HOUR", "9")),
        workday_end_hour=int(os.getenv("WORKDAY_END_HOUR", "18")),
        gemini_debug_logging=os.getenv("GEMINI_DEBUG_LOGGING", "").strip().casefold()
        in {"1", "true", "yes", "on"},
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip(),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip(),
        openrouter_proxy_url=os.getenv("OPENROUTER_PROXY_URL", outbound_proxy_url).strip(),
        digest_morning_hour=int(os.getenv("DIGEST_MORNING_HOUR", "10")),
        digest_evening_hour=int(os.getenv("DIGEST_EVENING_HOUR", "22")),
        celery_broker_url=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0").strip(),
        celery_result_backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1").strip(),
        celery_broker_data_dir=os.getenv("CELERY_BROKER_DATA_DIR", "data/celery_broker").strip(),
        celery_beat_schedule_path=os.getenv(
            "CELERY_BEAT_SCHEDULE_PATH", "data/celerybeat-schedule"
        ).strip(),
        webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/"),
        telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip(),
        persistent_backend=os.getenv("PERSISTENT_BACKEND", "file").strip().casefold(),
        supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
    )
