from __future__ import annotations

import base64
from collections.abc import Iterator, MutableMapping
from datetime import datetime, timezone
import pickle
from typing import Generic, TypeVar

from dayflow.config import Settings
from dayflow.crypto import decrypt_text, encrypt_text
from dayflow.supabase_client import SupabaseRestClient, build_supabase_client


T = TypeVar("T")


class SupabaseConversationStateStore:
    def __init__(self, client: SupabaseRestClient, encryption_key: str) -> None:
        if not encryption_key:
            raise RuntimeError("DATA_ENCRYPTION_KEY is required for persistent conversation state.")
        self.client = client
        self.encryption_key = encryption_key

    def load_all(self, state_type: str) -> dict[int, object]:
        rows = self.client.select(
            "conversation_states",
            params={"select": "chat_id,payload", "state_type": f"eq.{state_type}", "expires_at": "gt.now()"},
        )
        return {int(row["chat_id"]): self._decode(str(row["payload"])) for row in rows}

    def save(self, state_type: str, chat_id: int, value: object) -> None:
        self.client.upsert(
            "conversation_states",
            {"state_type": state_type, "chat_id": int(chat_id), "payload": self._encode(value)},
            on_conflict="state_type,chat_id",
        )

    def delete(self, state_type: str, chat_id: int) -> bool:
        return self.client.delete(
            "conversation_states",
            params={"state_type": f"eq.{state_type}", "chat_id": f"eq.{int(chat_id)}"},
        )

    def clear(self, state_type: str) -> None:
        self.client.delete("conversation_states", params={"state_type": f"eq.{state_type}"})

    def cleanup_expired(self) -> int:
        rows = self.client.select(
            "conversation_states",
            params={"select": "state_type,chat_id", "expires_at": "lt.now()"},
        )
        for row in rows:
            self.delete(str(row["state_type"]), int(row["chat_id"]))
        return len(rows)

    def _encode(self, value: object) -> str:
        raw = base64.b64encode(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)).decode("ascii")
        return encrypt_text(raw, self.encryption_key)

    def _decode(self, value: str) -> object:
        raw = decrypt_text(value, self.encryption_key)
        return pickle.loads(base64.b64decode(raw.encode("ascii")))


class PersistentStateDict(MutableMapping[int, T], Generic[T]):
    def __init__(self, state_type: str, store: SupabaseConversationStateStore) -> None:
        self.state_type = state_type
        self.store = store
        self.data: dict[int, T] = store.load_all(state_type)  # type: ignore[assignment]

    def __getitem__(self, key: int) -> T:
        return self.data[key]

    def __setitem__(self, key: int, value: T) -> None:
        self.data[int(key)] = value
        self.store.save(self.state_type, int(key), value)

    def __delitem__(self, key: int) -> None:
        del self.data[key]
        self.store.delete(self.state_type, key)

    def __iter__(self) -> Iterator[int]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def pop(self, key: int, default=None):
        if key not in self.data:
            return default
        value = self.data.pop(key)
        self.store.delete(self.state_type, key)
        return value

    def clear(self) -> None:
        self.data.clear()
        self.store.clear(self.state_type)


def build_conversation_state_dict(settings: Settings, state_type: str):
    if settings.persistent_backend != "supabase":
        return {}
    store = SupabaseConversationStateStore(
        build_supabase_client(settings),
        settings.data_encryption_key,
    )
    return PersistentStateDict(state_type, store)
