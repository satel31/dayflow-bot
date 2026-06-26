from __future__ import annotations

import json

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
    assert json.loads(response["body"]) == {"ok": True, "queued": "123"}
    assert queued == [{"update_id": 123}]
