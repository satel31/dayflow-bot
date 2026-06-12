from __future__ import annotations

from dataclasses import dataclass

from dayflow.config import Settings
from dayflow.supabase_client import SupabaseRestClient, build_supabase_client


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    chat_id: int
    timezone: str
    digest_morning_hour: int
    digest_evening_hour: int


class InMemoryUserProfileStore:
    def __init__(self) -> None:
        self.profiles: dict[int, UserProfile] = {}

    def get(self, user_id: int) -> UserProfile | None:
        return self.profiles.get(int(user_id))

    def list_profiles(self) -> list[UserProfile]:
        return list(self.profiles.values())

    def ensure(self, user_id: int, chat_id: int, settings: Settings) -> UserProfile:
        current = self.get(user_id)
        profile = UserProfile(
            user_id=int(user_id),
            chat_id=int(chat_id),
            timezone=current.timezone if current else settings.timezone,
            digest_morning_hour=current.digest_morning_hour if current else settings.digest_morning_hour,
            digest_evening_hour=current.digest_evening_hour if current else settings.digest_evening_hour,
        )
        self.profiles[profile.user_id] = profile
        return profile

    def save(self, profile: UserProfile) -> UserProfile:
        self.profiles[profile.user_id] = profile
        return profile


class SupabaseUserProfileStore:
    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    def get(self, user_id: int) -> UserProfile | None:
        rows = self.client.select(
            "user_profiles",
            params={
                "select": "user_id,chat_id,timezone,digest_morning_hour,digest_evening_hour",
                "user_id": f"eq.{int(user_id)}",
                "limit": "1",
            },
        )
        return _profile_from_row(rows[0]) if rows else None

    def list_profiles(self) -> list[UserProfile]:
        rows = self.client.select(
            "user_profiles",
            params={"select": "user_id,chat_id,timezone,digest_morning_hour,digest_evening_hour"},
        )
        return [_profile_from_row(row) for row in rows]

    def ensure(self, user_id: int, chat_id: int, settings: Settings) -> UserProfile:
        current = self.get(user_id)
        profile = UserProfile(
            user_id=int(user_id),
            chat_id=int(chat_id),
            timezone=current.timezone if current else settings.timezone,
            digest_morning_hour=current.digest_morning_hour if current else settings.digest_morning_hour,
            digest_evening_hour=current.digest_evening_hour if current else settings.digest_evening_hour,
        )
        return self.save(profile)

    def save(self, profile: UserProfile) -> UserProfile:
        self.client.upsert(
            "user_profiles",
            {
                "user_id": profile.user_id,
                "chat_id": profile.chat_id,
                "timezone": profile.timezone,
                "digest_morning_hour": profile.digest_morning_hour,
                "digest_evening_hour": profile.digest_evening_hour,
            },
            on_conflict="user_id",
        )
        return profile


def build_user_profile_store(settings: Settings):
    if settings.persistent_backend == "supabase":
        return SupabaseUserProfileStore(build_supabase_client(settings))
    if settings.persistent_backend != "file":
        raise ValueError(f"Unsupported PERSISTENT_BACKEND: {settings.persistent_backend}")
    return InMemoryUserProfileStore()


def _profile_from_row(row: dict) -> UserProfile:
    return UserProfile(
        user_id=int(row["user_id"]),
        chat_id=int(row["chat_id"]),
        timezone=str(row["timezone"]),
        digest_morning_hour=int(row["digest_morning_hour"]),
        digest_evening_hour=int(row["digest_evening_hour"]),
    )
