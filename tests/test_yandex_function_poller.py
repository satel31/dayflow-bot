from __future__ import annotations

import json

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
