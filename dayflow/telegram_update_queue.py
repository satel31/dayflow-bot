from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any

from dayflow.ydb_state_store import YdbStateStore


NAMESPACE = "telegram_update_queue"


@dataclass(frozen=True)
class TelegramUpdateEntry:
    key: str
    payload: dict[str, Any]
    attempts: int = 0


class TelegramUpdateQueue:
    def __init__(self, state: YdbStateStore) -> None:
        self.state = state

    def enqueue(self, payload: dict[str, Any]) -> str:
        update_id = payload.get("update_id")
        key = str(update_id) if update_id is not None else f"unknown:{int(time() * 1000)}"
        if self.state.get(NAMESPACE, key) is None:
            self.state.set(
                NAMESPACE,
                key,
                {
                    "payload": payload,
                    "attempts": 0,
                    "created_at": int(time()),
                    "last_error": "",
                },
            )
        return key

    def list_pending(self, limit: int) -> list[TelegramUpdateEntry]:
        entries = [
            TelegramUpdateEntry(
                key=key,
                payload=value["payload"],
                attempts=int(value.get("attempts", 0)),
            )
            for key, value in self.state.list(NAMESPACE).items()
        ]
        entries.sort(key=lambda item: _sort_key(item.key))
        return entries[:limit]

    def delete(self, key: str) -> None:
        self.state.delete(NAMESPACE, key)

    def record_failure(self, entry: TelegramUpdateEntry, error: str) -> None:
        self.state.set(
            NAMESPACE,
            entry.key,
            {
                "payload": entry.payload,
                "attempts": entry.attempts + 1,
                "created_at": int(time()),
                "last_error": error[:1000],
            },
        )


def _sort_key(key: str) -> tuple[int, str]:
    try:
        return (0, f"{int(key):020d}")
    except ValueError:
        return (1, key)
