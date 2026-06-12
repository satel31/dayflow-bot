from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from cryptography.fernet import Fernet

from dayflow.config import Settings
from dayflow.conversation_state_store import PersistentStateDict, SupabaseConversationStateStore
from dayflow.cron_service import MemoryDeliveryClaimStore, send_due_digests
from dayflow.crypto import decrypt_text, encrypt_text
from dayflow.user_profile_store import UserProfile


def make_settings(**overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "timezone": "Europe/Moscow",
        "google_calendar_id": "primary",
        "google_task_list_id": "@default",
        "event_groups_path": "data/event_groups.json",
        "google_credentials_path": "credentials.json",
        "google_token_path": "token.json",
        "google_tokens_dir": "data/google_tokens",
        "work_schedule_path": "data/work_schedule.json",
        "gemini_api_key": "",
        "gemini_model": "gemini-2.5-flash",
        "outbound_proxy_url": "",
        "telegram_proxy_url": "",
        "gemini_proxy_url": "",
        "workday_start_hour": 9,
        "workday_end_hour": 18,
        "gemini_debug_logging": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_encrypt_text_round_trip() -> None:
    key = Fernet.generate_key().decode()
    encrypted = encrypt_text('{"token":"secret"}', key)

    assert encrypted.startswith("fernet:")
    assert "secret" not in encrypted
    assert decrypt_text(encrypted, key) == '{"token":"secret"}'


def test_send_due_digests_uses_profile_timezone_and_is_idempotent() -> None:
    profiles = SimpleNamespace(
        list_profiles=lambda: [
            UserProfile(
                user_id=10,
                chat_id=100,
                timezone="Europe/Moscow",
                digest_morning_hour=10,
                digest_evening_hour=22,
            )
        ]
    )
    claims = MemoryDeliveryClaimStore()
    sent = []
    build_calls = []

    def build_digest(settings, user_id, kind):
        build_calls.append((settings.timezone, user_id, kind))
        return SimpleNamespace(text="digest")

    def send_message(settings, chat_id, text):
        sent.append((chat_id, text))

    now = datetime(2026, 6, 12, 7, 5, tzinfo=timezone.utc)
    first = send_due_digests(
        make_settings(),
        "morning",
        now=now,
        profile_store=profiles,
        claim_store=claims,
        build_digest=build_digest,
        send_message=send_message,
    )
    second = send_due_digests(
        make_settings(),
        "morning",
        now=now,
        profile_store=profiles,
        claim_store=claims,
        build_digest=build_digest,
        send_message=send_message,
    )

    assert first.sent == 1
    assert second.skipped == 1
    assert build_calls == [("Europe/Moscow", 10, "morning")]
    assert sent == [(100, "digest")]


def test_failed_digest_releases_delivery_claim() -> None:
    profiles = SimpleNamespace(
        list_profiles=lambda: [UserProfile(10, 100, "UTC", 7, 22)]
    )
    claims = MemoryDeliveryClaimStore()
    now = datetime(2026, 6, 12, 7, 0, tzinfo=timezone.utc)

    result = send_due_digests(
        make_settings(),
        "morning",
        now=now,
        profile_store=profiles,
        claim_store=claims,
        build_digest=lambda *args: (_ for _ in ()).throw(RuntimeError("fail")),
    )

    assert result.failed == 1
    assert not claims.claims


class FakeConversationClient:
    def __init__(self) -> None:
        self.rows = {}

    def select(self, table, *, params):
        state_type = params["state_type"].removeprefix("eq.")
        return [
            {"chat_id": chat_id, "payload": payload}
            for (stored_type, chat_id), payload in self.rows.items()
            if stored_type == state_type
        ]

    def upsert(self, table, payload, *, on_conflict):
        self.rows[(payload["state_type"], payload["chat_id"])] = payload["payload"]

    def delete(self, table, *, params):
        state_type = params["state_type"].removeprefix("eq.")
        chat_id = params.get("chat_id")
        keys = [
            key
            for key in self.rows
            if key[0] == state_type and (chat_id is None or key[1] == int(chat_id.removeprefix("eq.")))
        ]
        for key in keys:
            del self.rows[key]
        return bool(keys)


def test_persistent_conversation_state_survives_new_mapping() -> None:
    client = FakeConversationClient()
    key = Fernet.generate_key().decode()
    store = SupabaseConversationStateStore(client, key)
    first = PersistentStateDict("draft", store)

    first[100] = {"kind": "task_create", "title": "Купить молоко"}
    restored = PersistentStateDict("draft", SupabaseConversationStateStore(client, key))

    assert restored[100] == {"kind": "task_create", "title": "Купить молоко"}
    assert "Купить молоко" not in next(iter(client.rows.values()))
