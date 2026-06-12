from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dayflow.config import Settings
from dayflow.supabase_client import SupabaseRestClient, build_supabase_client


@dataclass(frozen=True)
class DigestSubscriber:
    user_id: int
    chat_id: int


class DigestSubscriberStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def list_subscribers(self) -> list[DigestSubscriber]:
        return [
            DigestSubscriber(user_id=int(item["user_id"]), chat_id=int(item["chat_id"]))
            for item in self._read()
        ]

    def add(self, user_id: int, chat_id: int) -> DigestSubscriber:
        subscribers = self._read()
        payload = {"user_id": int(user_id), "chat_id": int(chat_id)}
        for index, item in enumerate(subscribers):
            if int(item["user_id"]) == payload["user_id"]:
                subscribers[index] = payload
                self._write(subscribers)
                return DigestSubscriber(**payload)
        subscribers.append(payload)
        subscribers.sort(key=lambda item: int(item["user_id"]))
        self._write(subscribers)
        return DigestSubscriber(**payload)

    def remove(self, user_id: int) -> bool:
        subscribers = self._read()
        filtered = [item for item in subscribers if int(item["user_id"]) != int(user_id)]
        if len(filtered) == len(subscribers):
            return False
        self._write(filtered)
        return True

    def get(self, user_id: int) -> DigestSubscriber | None:
        for item in self._read():
            if int(item["user_id"]) == int(user_id):
                return DigestSubscriber(user_id=int(item["user_id"]), chat_id=int(item["chat_id"]))
        return None

    def _read(self) -> list[dict[str, int]]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Файл подписчиков рассылки должен содержать JSON-массив.")
        return payload

    def _write(self, data: list[dict[str, int]]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )


class SupabaseDigestSubscriberStore:
    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    def list_subscribers(self) -> list[DigestSubscriber]:
        rows = self.client.select("digest_subscribers", params={"select": "user_id,chat_id", "order": "user_id"})
        return [DigestSubscriber(user_id=int(row["user_id"]), chat_id=int(row["chat_id"])) for row in rows]

    def add(self, user_id: int, chat_id: int) -> DigestSubscriber:
        subscriber = DigestSubscriber(user_id=int(user_id), chat_id=int(chat_id))
        self.client.upsert(
            "digest_subscribers",
            {"user_id": subscriber.user_id, "chat_id": subscriber.chat_id},
            on_conflict="user_id",
        )
        return subscriber

    def remove(self, user_id: int) -> bool:
        return self.client.delete("digest_subscribers", params={"user_id": f"eq.{int(user_id)}"})

    def get(self, user_id: int) -> DigestSubscriber | None:
        rows = self.client.select(
            "digest_subscribers",
            params={"select": "user_id,chat_id", "user_id": f"eq.{int(user_id)}", "limit": "1"},
        )
        if not rows:
            return None
        return DigestSubscriber(user_id=int(rows[0]["user_id"]), chat_id=int(rows[0]["chat_id"]))


def build_digest_subscriber_store(settings: Settings):
    if settings.persistent_backend == "supabase":
        return SupabaseDigestSubscriberStore(build_supabase_client(settings))
    if settings.persistent_backend != "file":
        raise ValueError(f"Unsupported PERSISTENT_BACKEND: {settings.persistent_backend}")
    return DigestSubscriberStore(settings.digest_subscribers_path)
