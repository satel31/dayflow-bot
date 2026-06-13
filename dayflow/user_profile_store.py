from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from dayflow.config import Settings
from dayflow.ydb_state_store import build_ydb_state_store


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    chat_id: int
    timezone: str
    digest_morning_hour: int
    digest_evening_hour: int


class UserProfileStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def get(self, user_id: int) -> UserProfile | None:
        return next((profile for profile in self.list_profiles() if profile.user_id == int(user_id)), None)

    def list_profiles(self) -> list[UserProfile]:
        return [UserProfile(**item) for item in self._read()]

    def ensure(self, user_id: int, chat_id: int, settings: Settings) -> UserProfile:
        current = self.get(user_id)
        return self.save(
            UserProfile(
                user_id=int(user_id),
                chat_id=int(chat_id),
                timezone=current.timezone if current else settings.timezone,
                digest_morning_hour=current.digest_morning_hour if current else settings.digest_morning_hour,
                digest_evening_hour=current.digest_evening_hour if current else settings.digest_evening_hour,
            )
        )

    def save(self, profile: UserProfile) -> UserProfile:
        profiles = [item for item in self.list_profiles() if item.user_id != profile.user_id]
        profiles.append(profile)
        profiles.sort(key=lambda item: item.user_id)
        self._write([asdict(item) for item in profiles])
        return profile

    def _read(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: list[dict]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def build_user_profile_store(settings: Settings) -> UserProfileStore:
    if settings.storage_backend == "ydb":
        return YdbUserProfileStore(build_ydb_state_store(settings))
    return UserProfileStore(settings.user_profiles_path)


class YdbUserProfileStore:
    def __init__(self, state) -> None:
        self.state = state

    def get(self, user_id: int) -> UserProfile | None:
        payload = self.state.get("user_profiles", str(user_id))
        return UserProfile(**payload) if payload else None

    def list_profiles(self) -> list[UserProfile]:
        return [UserProfile(**payload) for payload in self.state.list("user_profiles").values()]

    def ensure(self, user_id: int, chat_id: int, settings: Settings) -> UserProfile:
        current = self.get(user_id)
        return self.save(
            UserProfile(
                user_id=int(user_id),
                chat_id=int(chat_id),
                timezone=current.timezone if current else settings.timezone,
                digest_morning_hour=current.digest_morning_hour if current else settings.digest_morning_hour,
                digest_evening_hour=current.digest_evening_hour if current else settings.digest_evening_hour,
            )
        )

    def save(self, profile: UserProfile) -> UserProfile:
        self.state.set("user_profiles", str(profile.user_id), asdict(profile))
        return profile
