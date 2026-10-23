from __future__ import annotations

import json
import urllib.error

from yandex_functions.telegram_poller import index


def test_poller_enqueues_updates_and_advances_offset(monkeypatch) -> None:
    state = {(index.STATE_NAMESPACE, index.OFFSET_KEY): 10}
    queued = []
    triggered = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(index, "_get_state", lambda namespace, key: state.get((namespace, key)))
    monkeypatch.setattr(
        index,
        "_set_state",
        lambda namespace, key, value: state.__setitem__((namespace, key), value),
    )
    monkeypatch.setattr(
        index,
        "_get_updates",
        lambda token, offset: [{"update_id": 10}, {"update_id": 11}],
    )
    monkeypatch.setattr(index, "_enqueue", queued.append)
    monkeypatch.setattr(index, "_trigger_process_queue", lambda: triggered.append(True) or True)

    response = index.handler({}, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["received"] == 2
    assert body["process_queue_triggered"] is True
    assert queued == [{"update_id": 10}, {"update_id": 11}]
    assert triggered == [True]
    assert state[(index.STATE_NAMESPACE, index.OFFSET_KEY)] == 12


def test_poller_does_not_trigger_queue_processor_without_updates(monkeypatch) -> None:
    state = {(index.STATE_NAMESPACE, index.OFFSET_KEY): 10}
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(index, "_get_state", lambda namespace, key: state.get((namespace, key)))
    monkeypatch.setattr(index, "_set_state", lambda namespace, key, value: None)
    monkeypatch.setattr(index, "_get_updates", lambda token, offset: [])

    def fail_trigger():
        raise AssertionError("queue processor should not be triggered")

    monkeypatch.setattr(index, "_trigger_process_queue", fail_trigger)

    response = index.handler({}, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["received"] == 0
    assert body["process_queue_triggered"] is False


def test_get_updates_fails_fast_by_default(monkeypatch) -> None:
    calls = []

    def fail_urlopen(request, timeout):
        calls.append(timeout)
        raise urllib.error.URLError("timed out")

    monkeypatch.delenv("TELEGRAM_GET_UPDATES_ATTEMPTS", raising=False)
    monkeypatch.delenv("TELEGRAM_GET_UPDATES_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(index.urllib.request, "urlopen", fail_urlopen)

    try:
        index._get_updates("token", 10)
    except RuntimeError as exc:
        assert "after retries" in str(exc)
    else:
        raise AssertionError("Expected getUpdates failure")

    assert calls == [index.GET_UPDATES_TIMEOUT_SECONDS]


def test_get_updates_allows_explicit_short_retry_budget(monkeypatch) -> None:
    calls = []

    def fail_urlopen(request, timeout):
        calls.append(timeout)
        raise urllib.error.URLError("timed out")

    monkeypatch.setenv("TELEGRAM_GET_UPDATES_ATTEMPTS", "2")
    monkeypatch.setenv("TELEGRAM_GET_UPDATES_TIMEOUT_SECONDS", "3")
    monkeypatch.setattr(index.urllib.request, "urlopen", fail_urlopen)

    try:
        index._get_updates("token", 10)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected getUpdates failure")

    assert calls == [3, 3]


def test_trigger_process_queue_uses_short_timeout(monkeypatch) -> None:
    timeouts = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        return Response()

    monkeypatch.setenv("PROCESS_QUEUE_URL", "https://bot.test/telegram/process-queue")
    monkeypatch.setenv("CRON_SECRET", "secret")
    monkeypatch.delenv("PROCESS_QUEUE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(index.urllib.request, "urlopen", fake_urlopen)

    assert index._trigger_process_queue() is True
    assert timeouts == [index.PROCESS_QUEUE_TIMEOUT_SECONDS]
