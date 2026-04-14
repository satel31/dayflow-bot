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
    )
