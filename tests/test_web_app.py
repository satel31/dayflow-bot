from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import bot
from dayflow.auth import GoogleAuthSession
from dayflow.config import Settings
from dayflow.cron_service import CronDigestResult
import dayflow.web_app as web_app_module
from dayflow.web_app import create_web_app


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages = []

    async def send_message(self, chat_id, text) -> None:
        self.sent_messages.append((chat_id, text))


class FakeTelegramApplication:
    def __init__(self) -> None:
        self.bot = FakeBot()
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
        "telegram_bot_username": "task_fox_bot",
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
    assert response.json() == {"status": "ok", "storage": "file"}


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


def test_google_oauth_callback_completes_auth_and_notifies_user(monkeypatch) -> None:
    session = GoogleAuthSession(
        auth_url="",
        state="oauth-state",
        redirect_uri="https://bot.test/google/oauth/callback",
        code_verifier="verifier",
    )
    store = SimpleNamespace(
        get_by_state=lambda state: (10, session) if state == "oauth-state" else None,
        delete=lambda user_id: True,
    )
    completed = []
    telegram_application = FakeTelegramApplication()
    monkeypatch.setattr(bot, "google_auth_session_store", store)
    monkeypatch.setattr(bot, "complete_google_auth", lambda *args: completed.append(args))
    monkeypatch.setattr(bot, "reset_user_google_services", lambda user_id: None)
    monkeypatch.setattr(bot.user_profile_store, "get", lambda user_id: SimpleNamespace(chat_id=100))
    app = create_web_app(settings=make_settings(), telegram_application=telegram_application)

    response = TestClient(app).get("/google/oauth/callback?state=oauth-state&code=code")

    assert response.status_code == 200
    assert completed
    assert telegram_application.bot.sent_messages == [(100, "Google-аккаунт подключен. Можно вернуться в Telegram.")]


def test_cron_endpoint_requires_secret_and_returns_result(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app_module,
        "send_due_digests",
        lambda settings, kind: CronDigestResult(due=2, sent=1, skipped=1, failed=0),
    )
    app = create_web_app(
        settings=make_settings(cron_secret="cron-secret"),
        telegram_application=FakeTelegramApplication(),
    )
    client = TestClient(app)

    assert client.post("/cron/morning-digest").status_code == 403
    response = client.post("/cron/morning-digest", headers={"X-Cron-Secret": "cron-secret"})

    assert response.status_code == 200
    assert response.json() == {"kind": "morning", "due": 2, "sent": 1, "skipped": 1, "failed": 0}
