from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from dayflow.config import Settings
from dayflow.web_app import create_web_app


class FakeTelegramApplication:
    def __init__(self) -> None:
        self.bot = SimpleNamespace()
        self.processed_updates = []

    async def process_update(self, update) -> None:
        self.processed_updates.append(update)


def make_settings(**overrides) -> Settings:
    values = {
        "telegram_bot_token": "test-token",
        "timezone": "Europe/Moscow",
        "google_calendar_id": "primary",
        "google_task_list_id": "@default",
        "event_groups_path": "data/event_groups.json",
        "google_credentials_path": "credentials.json",
        "google_token_path": "token.json",
        "google_tokens_dir": "data/google_tokens",
        "work_schedule_path": "data/work_schedule.json",
        "gemini_api_key": "",
        "gemini_model": "gemini-2.5-flash",
        "outbound_proxy_url": "",
        "telegram_proxy_url": "",
        "gemini_proxy_url": "",
        "workday_start_hour": 9,
        "workday_end_hour": 18,
        "gemini_debug_logging": False,
        "telegram_webhook_secret": "webhook-secret",
    }
    values.update(overrides)
    return Settings(**values)


def test_health_check() -> None:
    app = create_web_app(settings=make_settings(), telegram_application=FakeTelegramApplication())
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_rejects_invalid_secret() -> None:
    app = create_web_app(settings=make_settings(), telegram_application=FakeTelegramApplication())
    client = TestClient(app)

    response = client.post("/telegram/webhook", json={"update_id": 1})

    assert response.status_code == 403


def test_webhook_processes_telegram_update() -> None:
    telegram_application = FakeTelegramApplication()
    app = create_web_app(settings=make_settings(), telegram_application=telegram_application)
    client = TestClient(app)

    response = client.post(
        "/telegram/webhook",
        json={"update_id": 123},
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(telegram_application.processed_updates) == 1
    assert telegram_application.processed_updates[0].update_id == 123
