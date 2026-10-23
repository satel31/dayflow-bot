from __future__ import annotations

import json
import urllib.error

from yandex_functions.telegram_webhook import index


def test_function_rejects_invalid_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")

    response = index.handler(
        {
            "httpMethod": "POST",
            "headers": {"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            "body": "{}",
        },
        None,
    )

    assert response["statusCode"] == 403


def test_function_queues_update(monkeypatch) -> None:
    queued = []
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.delenv("PROCESS_QUEUE_URL", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.setattr(index, "_enqueue", lambda payload: queued.append(payload) or "123")

    response = index.handler(
        {
            "httpMethod": "POST",
            "headers": {"X-Telegram-Bot-Api-Secret-Token": "secret"},
            "body": json.dumps({"update_id": 123}),
        },
        None,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "ok": True,
        "queued": "123",
        "process_queue_triggered": False,
    }
    assert queued == [{"update_id": 123}]


def test_function_triggers_queue_processor_after_enqueue(monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("PROCESS_QUEUE_URL", "https://bot.test/telegram/process-queue")
    monkeypatch.setenv("CRON_SECRET", "cron-secret")
    monkeypatch.setenv("PROCESS_QUEUE_TIMEOUT_SECONDS", "2")
    monkeypatch.setattr(index, "_enqueue", lambda payload: "123")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"processed":1}'

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(index.urllib.request, "urlopen", fake_urlopen)

    response = index.handler(
        {
            "httpMethod": "POST",
            "headers": {"X-Telegram-Bot-Api-Secret-Token": "secret"},
            "body": json.dumps({"update_id": 123}),
        },
        None,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["process_queue_triggered"] is True
    assert calls == [("https://bot.test/telegram/process-queue?secret=cron-secret", 2)]


def test_function_returns_ok_when_queue_trigger_fails(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("PROCESS_QUEUE_URL", "https://bot.test/telegram/process-queue")
    monkeypatch.setenv("CRON_SECRET", "cron-secret")
    monkeypatch.setattr(index, "_enqueue", lambda payload: "123")

    def fail_urlopen(request, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(index.urllib.request, "urlopen", fail_urlopen)

    response = index.handler(
        {
            "httpMethod": "POST",
            "headers": {"X-Telegram-Bot-Api-Secret-Token": "secret"},
            "body": json.dumps({"update_id": 123}),
        },
        None,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["process_queue_triggered"] is False
