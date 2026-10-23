from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

import dayflow.spa_app as spa_module
from dayflow.auth import GoogleAuthSession
from dayflow.config import Settings


def make_settings() -> Settings:
    return Settings(
        telegram_bot_token="test-token",
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
    )


class FakeCalendar:
    created = []
    updated = []
    deleted = []

    def __init__(self, settings):
        pass

    def list_events_for_day(self, target):
        return [
            SimpleNamespace(
                event_id="event-1",
                title="Созвон",
                start=datetime.fromisoformat(f"{target}T10:00:00+03:00"),
                end=datetime.fromisoformat(f"{target}T11:00:00+03:00"),
                html_link="https://calendar.test/event-1",
                color_id="2",
            )
        ]

    def get_event_colors(self):
        return {"2": "#33b679"}

    def get_calendar_color(self):
        return "#4285f4"

    def find_free_slots(self, target, duration):
        return [
            (
                datetime.fromisoformat(f"{target}T12:00:00+03:00"),
                datetime.fromisoformat(f"{target}T14:00:00+03:00"),
            )
        ]

    def find_free_slots_in_range(self, start_date, end_date, **kwargs):
        return [
            (
                datetime.fromisoformat(f"{start_date}T12:00:00+03:00"),
                datetime.fromisoformat(f"{start_date}T14:00:00+03:00"),
            )
        ]

    def create_event(self, title, start_at, duration_minutes=60, color_id=None):
        self.created.append((title, start_at, duration_minutes, color_id))
        return SimpleNamespace(event_id="event-2")

    def update_event(self, event_id, **kwargs):
        self.updated.append((event_id, kwargs))
        return SimpleNamespace(event_id=event_id)

    def delete_event(self, event_id):
        self.deleted.append(event_id)


class FakeTasks:
    created = []

    def __init__(self, settings):
        pass

    def list_tasks(self, show_completed=False):
        return [
            SimpleNamespace(task_id="task-1", title="Ответить", due="2026-09-07", status="needsAction", tasklist_title="Входящие", notes="", parent=None, position="0001"),
            SimpleNamespace(task_id="task-2", title="Черновик", due="", status="needsAction", tasklist_title="Входящие", notes="Детали", parent="task-1", position="0002"),
            SimpleNamespace(task_id="task-3", title="Когда-нибудь", due="", status="needsAction", tasklist_title="Входящие", notes="", parent=None, position="0003"),
        ]

    def list_tasklists(self):
        return [
            {"id": "default", "title": "Мои задачи"},
            {"id": "work", "title": "Работа"},
        ]

    def create_task(self, title, **kwargs):
        self.created.append((title, kwargs))
        return "task-2"

    def complete_task(self, task_id, tasklist_name):
        return None

    def update_task(self, task_id, tasklist_name, **kwargs):
        return SimpleNamespace(task_id=task_id)

    def delete_task(self, task_id, tasklist_name):
        return None


def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(spa_module, "GoogleCalendarService", FakeCalendar)
    monkeypatch.setattr(spa_module, "GoogleTasksService", FakeTasks)
    return TestClient(spa_module.create_spa_app(settings=make_settings()))


def test_spa_and_today_api(monkeypatch) -> None:
    web = client(monkeypatch)
    page = web.get("/")
    assert page.status_code == 200
    assert 'id="weekView"' in page.text
    assert "button disabled" not in page.text
    response = web.get("/api/today?target=2026-09-07")
    assert response.status_code == 200
    assert response.json()["events"][0]["title"] == "Созвон"
    assert response.json()["events"][0]["color"] == "2"
    assert response.json()["colors"] == {"2": "#33b679"}
    assert response.json()["default_color"] == "#4285f4"
    assert response.json()["tasks"][0]["title"] == "Ответить"
    assert response.json()["tasks"][1]["parent"] == "task-1"


def test_week_api_returns_all_seven_days(monkeypatch) -> None:
    web = client(monkeypatch)
    response = web.get("/api/week?start=2026-09-09")
    assert response.status_code == 200
    assert response.json()["start"] == "2026-09-07"
    assert response.json()["end"] == "2026-09-13"
    assert len(response.json()["days"]) == 7


def test_week_api_uses_monday_through_sunday(monkeypatch) -> None:
    web = client(monkeypatch)
    response = web.get("/api/week?start=2026-09-09")
    data = response.json()

    assert response.status_code == 200
    assert data["start"] == "2026-09-07"
    assert data["end"] == "2026-09-13"
    assert [day["date"] for day in data["days"]] == [
        "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10",
        "2026-09-11", "2026-09-12", "2026-09-13",
    ]
    assert all(day["events"][0]["title"] == "Созвон" for day in data["days"])


def test_slots_and_task_creation(monkeypatch) -> None:
    FakeTasks.created.clear()
    web = client(monkeypatch)
    assert web.get("/api/slots?target=2099-09-07&duration=60").json()["slots"][0]["start"].endswith("+03:00")
    lists = web.get("/api/tasklists")
    response = web.post("/api/tasks", json={"title": "Новая задача", "due_date": "2026-09-08", "tasklist_name": "Работа"})
    assert lists.json()["tasklists"][1] == {"id": "work", "title": "Работа"}
    assert response.status_code == 201
    assert response.json() == {"id": "task-2", "status": "created"}
    assert FakeTasks.created[0][0] == "Новая задача"
    assert FakeTasks.created[0][1]["tasklist_name"] == "Работа"


def test_slots_support_date_and_time_range(monkeypatch) -> None:
    web = client(monkeypatch)
    response = web.get(
        "/api/slots?target=2099-09-07&end_date=2099-09-10&start_time=07:00&end_time=22:00&duration=90"
    )

    assert response.status_code == 200
    assert response.json()["date"] == "2099-09-07"
    assert response.json()["end_date"] == "2099-09-10"
    assert web.get("/api/slots?target=2099-09-10&end_date=2099-09-07").status_code == 422


def test_today_slots_start_at_next_quarter_hour(monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 7, 16, 10, tzinfo=tz)

    class WideOpenCalendar(FakeCalendar):
        def find_free_slots_in_range(self, start_date, end_date, **kwargs):
            return [(
                datetime.fromisoformat("2026-09-07T07:00:00+03:00"),
                datetime.fromisoformat("2026-09-07T19:00:00+03:00"),
            )]

    monkeypatch.setattr(spa_module, "datetime", FixedDateTime)
    monkeypatch.setattr(spa_module, "GoogleCalendarService", WideOpenCalendar)
    monkeypatch.setattr(spa_module, "GoogleTasksService", FakeTasks)
    web = TestClient(spa_module.create_spa_app(settings=make_settings()))

    response = web.get("/api/slots?target=2026-09-07&duration=60")

    assert response.json()["slots"][0]["start"] == "2026-09-07T16:15:00+03:00"
    assert response.json()["slots"][0]["end"] == "2026-09-07T19:00:00+03:00"


def test_task_date_listing_update_and_delete(monkeypatch) -> None:
    web = client(monkeypatch)
    listed = web.get("/api/tasks?target=2026-09-07")
    updated = web.put("/api/tasks/Входящие/task-1", json={"title": "Новый заголовок", "due_date": "2026-09-09", "notes": "Текст"})
    deleted = web.delete("/api/tasks/Входящие/task-1")

    assert listed.status_code == 200
    assert listed.json()["tasks"][1]["parent"] == "task-1"
    assert listed.json()["tasks"][0]["position"] == "0001"
    random_day_tasks = web.get("/api/tasks?target=2026-10-22").json()["tasks"]
    assert [item["id"] for item in random_day_tasks] == ["task-3"]
    assert updated.json() == {"id": "task-1", "status": "updated"}
    assert deleted.json() == {"id": "task-1", "status": "deleted"}


def test_event_creation_uses_configured_timezone(monkeypatch) -> None:
    FakeCalendar.created.clear()
    web = client(monkeypatch)
    response = web.post("/api/events", json={
        "title": "Встреча",
        "event_date": "2026-09-09",
        "start_time": "15:30",
        "duration_minutes": 90,
        "group_name": "Спорт",
    })

    assert response.status_code == 201
    assert response.json() == {"id": "event-2", "status": "created"}
    assert FakeCalendar.created[0][0] == "Встреча"
    assert FakeCalendar.created[0][1].isoformat() == "2026-09-09T15:30:00+03:00"
    assert FakeCalendar.created[0][2] == 90
    assert FakeCalendar.created[0][3] == "11"


def test_event_groups_and_exact_suggestion(monkeypatch) -> None:
    web = client(monkeypatch)
    groups = web.get("/api/event-groups").json()["groups"]
    suggestion = web.get("/api/event-groups/suggest?title=Тренировка Спорт").json()

    assert {item["name"] for item in groups} >= {"Спорт", "Встречи"}
    assert suggestion == {"group": "Спорт"}


def test_event_group_creation(monkeypatch) -> None:
    stored = {}

    class FakeGroupStore:
        def list_groups(self):
            return stored.copy()

        def add_group(self, name, color):
            stored[name] = color
            return name, color

        def resolve_color_id(self, name):
            return stored.get(name)

        def delete_group(self, name):
            return stored.pop(name, None) is not None

        def update_group(self, current_name, new_name, color):
            if current_name not in stored:
                raise KeyError(current_name)
            del stored[current_name]
            stored[new_name] = color
            return new_name, color

    monkeypatch.setattr(spa_module, "build_event_group_store", lambda settings: FakeGroupStore())
    web = client(monkeypatch)

    created = web.post("/api/event-groups", json={"name": "Учёба", "color": "3"})

    assert created.status_code == 201
    assert created.json() == {"name": "Учёба", "color": "3"}
    assert web.get("/api/event-groups").json()["groups"] == [{"name": "Учёба", "color": "3"}]
    updated = web.put("/api/event-groups/Учёба", json={"name": "Курсы", "color": "5"})
    assert updated.json() == {"name": "Курсы", "color": "5", "status": "updated"}
    deleted = web.delete("/api/event-groups/Курсы")
    assert deleted.json() == {"name": "Курсы", "status": "deleted"}
    assert web.get("/api/event-groups").json()["groups"] == []


def test_yoga_group_is_suggested_by_ai(monkeypatch) -> None:
    class FakeAssistant:
        enabled = True

        def __init__(self, settings):
            pass

        def plan(self, prompt, event_groups):
            assert "Yoga" in prompt
            assert "Спорт" in event_groups
            return SimpleNamespace(event_group="Спорт")

    monkeypatch.setattr(spa_module, "AssistantService", FakeAssistant)
    web = client(monkeypatch)

    assert web.get("/api/event-groups/suggest?title=Yoga").json() == {"group": "Спорт"}


def test_event_update_and_delete(monkeypatch) -> None:
    FakeCalendar.updated.clear()
    FakeCalendar.deleted.clear()
    web = client(monkeypatch)
    payload = {
        "title": "Перенесённая встреча",
        "event_date": "2026-09-10",
        "start_time": "16:00",
        "duration_minutes": 60,
    }

    updated = web.put("/api/events/event-1", json=payload)
    deleted = web.delete("/api/events/event-1")

    assert updated.json() == {"id": "event-1", "status": "updated"}
    assert FakeCalendar.updated[0][1]["new_start_at"].isoformat() == "2026-09-10T16:00:00+03:00"
    assert deleted.json() == {"id": "event-1", "status": "deleted"}
    assert FakeCalendar.deleted == ["event-1"]


def test_google_connect_and_callback(monkeypatch) -> None:
    web = client(monkeypatch)
    session = GoogleAuthSession(
        auth_url="https://accounts.google.test/auth",
        state="oauth-state",
        redirect_uri="http://testserver/google/oauth/callback",
        code_verifier="verifier",
    )
    completed = []
    monkeypatch.setattr(spa_module, "build_google_auth_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(spa_module, "complete_google_auth", lambda *args: completed.append(args))

    connect = web.get("/api/google/connect")
    callback = web.get("/google/oauth/callback?state=oauth-state&code=code")

    assert connect.json() == {"auth_url": "https://accounts.google.test/auth"}
    assert callback.status_code == 200
    assert completed
