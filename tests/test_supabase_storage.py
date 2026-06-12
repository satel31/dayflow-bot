from __future__ import annotations

import json

import httpx

from dayflow import auth
from dayflow.config import Settings
from dayflow.digest_subscriber_store import SupabaseDigestSubscriberStore
from dayflow.group_store import SupabaseEventGroupStore
from dayflow.supabase_client import SupabaseRestClient
from dayflow.work_schedule_store import SupabaseWorkScheduleStore, WorkSchedule


def test_supabase_digest_subscriber_store_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.params.get("limit") == "1":
            return httpx.Response(200, json=[{"user_id": 10, "chat_id": 101}])
        if request.method == "GET":
            return httpx.Response(200, json=[{"user_id": 10, "chat_id": 101}])
        if request.method == "POST":
            return httpx.Response(201)
        if request.method == "DELETE":
            return httpx.Response(200, json=[{"user_id": 10, "chat_id": 101}])
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = SupabaseRestClient(
        "https://project.supabase.co",
        "service-role-key",
        transport=httpx.MockTransport(handler),
    )
    store = SupabaseDigestSubscriberStore(client)

    assert store.add(10, 101).chat_id == 101
    assert store.get(10).chat_id == 101
    assert store.list_subscribers()[0].user_id == 10
    assert store.remove(10) is True

    post_request = next(request for request in requests if request.method == "POST")
    assert json.loads(post_request.content) == {"user_id": 10, "chat_id": 101}
    assert post_request.url.params["on_conflict"] == "user_id"
    assert post_request.headers["apikey"] == "service-role-key"


def test_supabase_delete_returns_false_when_no_rows_deleted() -> None:
    client = SupabaseRestClient(
        "https://project.supabase.co",
        "service-role-key",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    assert client.delete("google_tokens", params={"user_id": "eq.10"}) is False


class FakeStateClient:
    def __init__(self) -> None:
        self.state = {}

    def get_app_state(self, key):
        return self.state.get(key)

    def set_app_state(self, key, value):
        self.state[key] = value


def test_supabase_app_state_stores_groups_and_work_schedule() -> None:
    client = FakeStateClient()
    group_store = SupabaseEventGroupStore(client)
    schedule_store = SupabaseWorkScheduleStore(client, 9, 18)

    group_store.add_group("Работа", "синий")
    schedule_store.save(WorkSchedule(weekdays=(0, 2, 4), start_minutes=600, end_minutes=1140))

    assert group_store.resolve_color_id("работа") == "9"
    assert schedule_store.load() == WorkSchedule(
        weekdays=(0, 2, 4),
        start_minutes=600,
        end_minutes=1140,
    )


class FakeTokenClient:
    def __init__(self) -> None:
        self.rows = {}

    def select(self, table, *, params=None):
        user_id = int(params["user_id"].removeprefix("eq."))
        row = self.rows.get(user_id)
        if not row:
            return []
        selected = params["select"]
        return [{selected: row[selected]}]

    def upsert(self, table, payload, *, on_conflict):
        self.rows[payload["user_id"]] = payload

    def delete(self, table, *, params):
        user_id = int(params["user_id"].removeprefix("eq."))
        return self.rows.pop(user_id, None) is not None


def make_supabase_settings() -> Settings:
    return Settings(
        telegram_bot_token="token",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
        persistent_backend="supabase",
        supabase_url="https://project.supabase.co",
        supabase_service_role_key="service-role-key",
    )


def test_supabase_google_token_storage(monkeypatch) -> None:
    client = FakeTokenClient()
    monkeypatch.setattr(auth, "build_supabase_client", lambda settings: client)
    settings = make_supabase_settings()
    token_json = json.dumps({"token": "secret"})

    auth._write_token_json(settings, 10, token_json)

    assert auth.google_token_exists(settings, 10) is True
    assert json.loads(auth._read_token_json(settings, 10)) == {"token": "secret"}
    assert auth.disconnect_google_account(settings, 10) is True
    assert auth.google_token_exists(settings, 10) is False
