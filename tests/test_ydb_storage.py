from __future__ import annotations

from cryptography.fernet import Fernet

from dayflow.auth import GoogleAuthSession
from dayflow.digest_subscriber_store import YdbDigestSubscriberStore
from dayflow.google_auth_session_store import YdbGoogleAuthSessionStore
from dayflow.group_store import YdbEventGroupStore
from dayflow.user_profile_store import UserProfile, YdbUserProfileStore
from dayflow.work_schedule_store import WorkSchedule, YdbWorkScheduleStore
from dayflow.ydb_state_store import YdbStateStore


class FakeYdbState:
    def __init__(self) -> None:
        self.data = {}

    def get(self, namespace, key):
        return self.data.get((namespace, key))

    def list(self, namespace):
        return {key: value for (stored_namespace, key), value in self.data.items() if stored_namespace == namespace}

    def set(self, namespace, key, value):
        self.data[(namespace, key)] = value

    def delete(self, namespace, key):
        return self.data.pop((namespace, key), None) is not None


def test_ydb_subscribers_profiles_groups_and_schedule() -> None:
    state = FakeYdbState()
    subscribers = YdbDigestSubscriberStore(state)
    profiles = YdbUserProfileStore(state)
    groups = YdbEventGroupStore(state)
    schedule = YdbWorkScheduleStore(state, 9, 18)

    subscribers.add(10, 100)
    profiles.save(UserProfile(10, 100, "Europe/Moscow", 10, 22))
    groups.add_group("Работа", "синий")
    schedule.save(WorkSchedule((0, 2, 4), 600, 1140))

    assert subscribers.get(10).chat_id == 100
    assert profiles.get(10).timezone == "Europe/Moscow"
    assert groups.resolve_color_id("работа") == "9"
    assert schedule.load() == WorkSchedule((0, 2, 4), 600, 1140)


def test_ydb_oauth_session_survives_store_instance_and_encrypts_verifier() -> None:
    state = FakeYdbState()
    key = Fernet.generate_key().decode()
    session = GoogleAuthSession(
        auth_url="",
        state="oauth-state",
        redirect_uri="https://bot.test/google/oauth/callback",
        code_verifier="secret-verifier",
    )

    YdbGoogleAuthSessionStore(state, key).save(10, session)
    restored = YdbGoogleAuthSessionStore(state, key).get_by_state("oauth-state")

    assert restored is not None
    assert restored[1].code_verifier == "secret-verifier"
    assert "secret-verifier" not in str(state.data)


def test_ydb_state_store_passes_yql_parameters() -> None:
    store = YdbStateStore.__new__(YdbStateStore)
    captured = {}

    def execute(query, params):
        captured.update(params)
        return []

    store._execute = execute

    store.set("namespace", "key", {"value": 1})

    assert set(captured) == {"$namespace", "$key", "$value"}
    assert captured["$namespace"] == "namespace"
    assert captured["$key"] == "key"
    assert captured["$value"] == '{"value": 1}'
