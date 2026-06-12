from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dayflow.config import Settings


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


def build_digest_subscriber_store(settings: Settings):
    return DigestSubscriberStore(settings.digest_subscribers_path)
