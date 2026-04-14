from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from googleapiclient.discovery import build

from dayflow.auth import load_google_credentials
from dayflow.config import Settings
from dayflow.timezone_utils import get_timezone


@dataclass(frozen=True)
class TaskItem:
    task_id: str
    title: str
    tasklist_id: str
    tasklist_title: str
    notes: str
    due: str
    status: str
    parent: str | None = None


class GoogleTasksService:
    def __init__(self, settings: Settings, user_id: int | None = None) -> None:
        self.settings = settings
        self.user_id = user_id
        self.tz = get_timezone(settings.timezone)
        self._service = None

    def list_tasklists(self) -> list[dict[str, str]]:
        response = self._get_service().tasklists().list().execute()
        items = response.get("items", [])
        return [{"id": item["id"], "title": item.get("title", "")} for item in items]

    def list_tasks(self, tasklist_name: str | None = None, show_completed: bool = True) -> list[TaskItem]:
        tasklists = [self.resolve_tasklist(tasklist_name)] if tasklist_name else self.list_tasklists()
        tasks: list[TaskItem] = []
        for tasklist in tasklists:
            response = (
                self._get_service()
                .tasks()
                .list(tasklist=tasklist["id"], showCompleted=show_completed, showHidden=False)
                .execute()
            )
            for item in response.get("items", []):
                tasks.append(self._to_task_item(item, tasklist))
        return tasks

    def search_tasks(
        self,
        title: str,
        tasklist_name: str | None = None,
        *,
        show_completed: bool = True,
    ) -> list[TaskItem]:
        title_cf = title.strip().casefold()
        return [
            item
            for item in self.list_tasks(tasklist_name=tasklist_name, show_completed=show_completed)
            if title_cf in item.title.casefold()
            and (show_completed or item.status != "completed")
        ]

    def resolve_tasklist(self, name: str | None) -> dict[str, str]:
        lists = self.list_tasklists()
        if not name:
            for item in lists:
                if item["id"] == self.settings.google_task_list_id:
                    return item
            if lists:
                return lists[0]
            raise RuntimeError("В Google Tasks не найдено ни одного списка.")

        for item in lists:
            if item["title"].casefold() == name.strip().casefold():
                return item

        available = ", ".join(item["title"] for item in lists) or "списки не найдены"
        raise RuntimeError(f'Список "{name}" не найден. Доступно: {available}')

    def create_task(
        self,
        title: str,
        due_date: date | None = None,
        notes: str = "",
        tasklist_name: str | None = None,
        parent_task_id: str | None = None,
    ) -> str:
        tasklist = self.resolve_tasklist(tasklist_name)
        body = {"title": title}
        if notes:
            body["notes"] = notes
        if due_date:
            due_at = datetime.combine(due_date, time(12, 0), tzinfo=self.tz)
            body["due"] = due_at.isoformat()
        created = (
            self._get_service()
            .tasks()
            .insert(
                tasklist=tasklist["id"],
                parent=parent_task_id,
                body=body,
            )
            .execute()
        )
        return created.get("id", "")

    def create_task_with_subtasks(
        self,
        title: str,
        due_date: date | None = None,
        notes: str = "",
        tasklist_name: str | None = None,
        subtasks: list[str] | None = None,
    ) -> tuple[str, str]:
        tasklist = self.resolve_tasklist(tasklist_name)
        parent_id = self.create_task(title, due_date, notes, tasklist["title"])
        for subtask in subtasks or []:
            clean_title = subtask.strip()
            if clean_title:
                self.create_task(
                    clean_title,
                    due_date=None,
                    notes="",
                    tasklist_name=tasklist["title"],
                    parent_task_id=parent_id,
                )
        return parent_id, tasklist["title"]

    def update_task(
        self,
        task_id: str,
        tasklist_name: str,
        *,
        new_title: str | None = None,
        due_date: date | None = None,
        notes: str | None = None,
    ) -> TaskItem:
        tasklist = self.resolve_tasklist(tasklist_name)
        existing = (
            self._get_service()
            .tasks()
            .get(tasklist=tasklist["id"], task=task_id)
            .execute()
        )
        body = {
            "id": task_id,
            "title": new_title or existing.get("title", ""),
            "status": existing.get("status", "needsAction"),
        }
        if notes is not None:
            body["notes"] = notes
        elif "notes" in existing:
            body["notes"] = existing.get("notes", "")
        if due_date:
            due_at = datetime.combine(due_date, time(12, 0), tzinfo=self.tz)
            body["due"] = due_at.isoformat()
        elif existing.get("due"):
            body["due"] = existing["due"]
        updated = (
            self._get_service()
            .tasks()
            .update(tasklist=tasklist["id"], task=task_id, body=body)
            .execute()
        )
        return self._to_task_item(updated, tasklist)

    def complete_task(self, task_id: str, tasklist_name: str) -> TaskItem:
        tasklist = self.resolve_tasklist(tasklist_name)
        existing = (
            self._get_service()
            .tasks()
            .get(tasklist=tasklist["id"], task=task_id)
            .execute()
        )
        body = {
            "id": task_id,
            "title": existing.get("title", ""),
            "status": "completed",
            "notes": existing.get("notes", ""),
        }
        if existing.get("due"):
            body["due"] = existing["due"]
        updated = (
            self._get_service()
            .tasks()
            .update(tasklist=tasklist["id"], task=task_id, body=body)
            .execute()
        )
        return self._to_task_item(updated, tasklist)

    def delete_task(self, task_id: str, tasklist_name: str) -> None:
        tasklist = self.resolve_tasklist(tasklist_name)
        (
            self._get_service()
            .tasks()
            .delete(tasklist=tasklist["id"], task=task_id)
            .execute()
        )

    def _get_service(self):
        if self._service is None:
            creds = load_google_credentials(self.settings, self.user_id)
            self._service = build("tasks", "v1", credentials=creds)
        return self._service

    def _to_task_item(self, item: dict, tasklist: dict[str, str]) -> TaskItem:
        return TaskItem(
            task_id=item["id"],
            title=item.get("title", ""),
            tasklist_id=tasklist["id"],
            tasklist_title=tasklist["title"],
            notes=item.get("notes", ""),
            due=item.get("due", ""),
            status=item.get("status", "needsAction"),
            parent=item.get("parent"),
        )
