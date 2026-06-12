from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from cryptography.fernet import Fernet

from dayflow.config import Settings
from dayflow.cron_service import FileDeliveryClaimStore, send_due_digests
from dayflow.crypto import decrypt_text, encrypt_text
from dayflow.google_auth_session_store import GoogleAuthSessionStore
from dayflow.auth import GoogleAuthSession
from dayflow.user_profile_store import UserProfile, UserProfileStore


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


def test_send_due_digests_uses_profile_timezone_and_is_idempotent(tmp_path) -> None:
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
    claims = FileDeliveryClaimStore(str(tmp_path / "claims.json"))
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


def test_failed_digest_releases_delivery_claim(tmp_path) -> None:
    profiles = SimpleNamespace(
        list_profiles=lambda: [UserProfile(10, 100, "UTC", 7, 22)]
    )
    claims = FileDeliveryClaimStore(str(tmp_path / "claims.json"))
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
    assert claims._read() == set()


def test_file_user_profile_store_survives_new_instance(tmp_path) -> None:
    path = str(tmp_path / "profiles.json")
    first = UserProfileStore(path)
    profile = UserProfile(10, 100, "Europe/Moscow", 10, 22)

    first.save(profile)

    assert UserProfileStore(path).get(10) == profile


def test_file_google_auth_session_store_survives_restart_and_encrypts_verifier(tmp_path) -> None:
    path = str(tmp_path / "oauth_sessions.json")
    key = Fernet.generate_key().decode()
    session = GoogleAuthSession(
        auth_url="https://accounts.google.test/auth",
        state="state",
        redirect_uri="https://bot.test/google/oauth/callback",
        code_verifier="secret-verifier",
    )

    GoogleAuthSessionStore(path, key).save(10, session)
    restored = GoogleAuthSessionStore(path, key).get_by_state("state")

    assert restored is not None
    assert restored[0] == 10
    assert restored[1].code_verifier == "secret-verifier"
    assert "secret-verifier" not in (tmp_path / "oauth_sessions.json").read_text(encoding="utf-8")
