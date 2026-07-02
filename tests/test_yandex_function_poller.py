from __future__ import annotations

import json

from yandex_functions.telegram_poller import index


def test_poller_enqueues_updates_and_advances_offset(monkeypatch) -> None:
    state = {(index.STATE_NAMESPACE, index.OFFSET_KEY): 10}
    queued = []
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

    response = index.handler({}, None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["received"] == 2
    assert queued == [{"update_id": 10}, {"update_id": 11}]
    assert state[(index.STATE_NAMESPACE, index.OFFSET_KEY)] == 12
