import pytest

from dayflow.digest_subscriber_store import DigestSubscriberStore


def test_digest_subscriber_store_add_update_list_and_remove(tmp_path):
    store = DigestSubscriberStore(str(tmp_path / "subscribers.json"))

    store.add(user_id=20, chat_id=200)
    store.add(user_id=10, chat_id=100)
    store.add(user_id=20, chat_id=201)

    assert [(item.user_id, item.chat_id) for item in store.list_subscribers()] == [
        (10, 100),
        (20, 201),
    ]
    assert store.get(20).chat_id == 201
    assert store.remove(10) is True
    assert store.remove(10) is False
    assert [(item.user_id, item.chat_id) for item in store.list_subscribers()] == [(20, 201)]


def test_digest_subscriber_store_rejects_non_list_payload(tmp_path):
    path = tmp_path / "subscribers.json"
    path.write_text("{}", encoding="utf-8")
    store = DigestSubscriberStore(str(path))

    with pytest.raises(ValueError, match="JSON-массив"):
        store.list_subscribers()
