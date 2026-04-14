from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dayflow.config import Settings


logger = logging.getLogger(__name__)


class AssistantServiceError(RuntimeError):
    """User-facing assistant error."""


@dataclass(frozen=True)
class AssistantPlan:
    action: str
    reply: str
    date: str = ""
    date_from: str = ""
    date_to: str = ""
    time: str = ""
    duration_minutes: int = 60
    preferred_period: str = ""
    excluded_period: str = ""
    weekday_filter: str = ""
    within_work_hours: bool = False
    title: str = ""
    target_title: str = ""
    new_title: str = ""
    notes: str = ""
    event_group: str = ""
    task_list: str = ""
    outside_work_hours: bool = False
    subtasks: tuple[str, ...] = ()


class AssistantService:
    _SYSTEM_INSTRUCTION = (
        "Ты DayFlow, Telegram-ассистент для личного планирования.\n"
        "Определи ровно одно действие по сообщению пользователя.\n"
        "Доступные действия: chat, list_events, find_free_slots, create_event, "
        "update_event, delete_event, create_task, update_task, delete_task, complete_task.\n"
        "Правила выбора действия:\n"
        "- расписание на день -> list_events\n"
        "- свободное окно, слот, время -> find_free_slots\n"
        "- запланировать встречу, созвон, тренировку, визит -> create_event\n"
        "- перенести, изменить, переименовать событие -> update_event\n"
        "- удалить или отменить событие -> delete_event\n"
        "- добавить задачу, todo, напоминание -> create_task\n"
        "- изменить, перенести, переименовать задачу -> update_task\n"
        "- удалить задачу -> delete_task\n"
        "- отметить задачу выполненной -> complete_task\n"
        "Если данных не хватает, ничего не выдумывай: верни action=chat и короткий reply с уточнением.\n"
        "Относительные даты переводи в ISO YYYY-MM-DD.\n"
        "Если пользователь указывает диапазон вроде 'через 3-4 недели', заполни date_from и date_to.\n"
        "Если время не указано и оно обязательно, верни action=chat с уточнением.\n"
        "Если пользователь хочет записаться или запланировать что-то, но точное время не указал, "
        "а вместо этого дал диапазон дат или ограничения по времени, выбери find_free_slots, а не create_event.\n"
        "Если пользователь пишет про время суток, заполни preferred_period одним из: morning, day, evening, night.\n"
        "Если пользователь пишет отрицание вроде 'не утром', заполни excluded_period одним из: morning, day, evening, night.\n"
        "Если пользователь пишет 'по будням' или 'в выходные', заполни weekday_filter значением workdays или weekend.\n"
        "Если пользователь просит подобрать время в рабочее время, установи within_work_hours=true.\n"
        "Для update_event и delete_event сначала определи target_title. "
        "Даже без новой даты или времени возвращай соответствующее действие, если цель понятна.\n"
        "Если длительность не указана, используй 60 минут для событий и слотов.\n"
        "Если пользователь просит подобрать время не в рабочее время, установи outside_work_hours=true.\n"
        "Для событий укажи event_group, только если это очевидно.\n"
        "Для задач укажи task_list, только если пользователь указал список или он очевиден.\n"
        "Если пользователь явно просит добавить описание или заметку к задаче "
        "формулировками вроде 'в заметке', 'в описании', 'добавь заметку', 'заметка: ...', "
        "обязательно заполни поле notes этим текстом.\n"
        "Если задача составная, выдели подзадачи в subtasks.\n"
        "Для update/delete target_title это текущее название или главный идентификатор записи.\n"
        "Для update title или new_title используй как новое название, если оно явно есть.\n"
        "Поле reply пиши по-человечески: коротко, тепло, естественно, без канцелярита. "
        "Можно добавить 0-2 уместных эмодзи, но без перебора.\n"
        "Ответь строго валидным JSON без markdown и без пояснений."
    )
    _RESPONSE_SCHEMA: dict[str, Any] = {
        "type": "object",
        "required": ["action", "reply"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "chat",
                    "list_events",
                    "find_free_slots",
                    "create_event",
                    "update_event",
                    "delete_event",
                    "create_task",
                    "update_task",
                    "delete_task",
                    "complete_task",
                ],
            },
            "reply": {"type": "string"},
            "date": {"type": "string"},
            "date_from": {"type": "string"},
            "date_to": {"type": "string"},
            "time": {"type": "string"},
            "duration_minutes": {"type": "integer"},
            "preferred_period": {"type": "string"},
            "excluded_period": {"type": "string"},
            "weekday_filter": {"type": "string"},
            "within_work_hours": {"type": "boolean"},
            "title": {"type": "string"},
            "target_title": {"type": "string"},
            "new_title": {"type": "string"},
            "notes": {"type": "string"},
            "event_group": {"type": "string"},
            "task_list": {"type": "string"},
            "outside_work_hours": {"type": "boolean"},
            "subtasks": {"type": "array", "items": {"type": "string"}},
        },
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = None
        self._client_provider = ""

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openrouter_api_key or self.settings.gemini_api_key)

    def _provider_name(self) -> str:
        if self.settings.openrouter_api_key:
            return "openrouter"
        return "gemini"

    def plan(self, message_text: str) -> AssistantPlan:
        if not self.enabled:
            return AssistantPlan(
                action="chat",
                reply=(
                    "Нужен OPENROUTER_API_KEY или GEMINI_API_KEY, чтобы понимать свободные запросы и самой "
                    "решать, проверить слоты, создать событие или задачу."
                ),
            )

        if self._provider_name() == "openrouter":
            return self._plan_with_openrouter(message_text)
        return self._plan_with_gemini(message_text)

    def _build_user_prompt(self, message_text: str) -> str:
        return (
            f"Часовой пояс пользователя: {self.settings.timezone}.\n"
            f"Текущие дата и время: {datetime.now().astimezone().isoformat()}.\n"
            "Верни JSON с полями: action, reply, date, time, duration_minutes, preferred_period, "
            "excluded_period, weekday_filter, within_work_hours, title, "
            "target_title, new_title, notes, event_group, task_list, date_from, date_to, "
            "outside_work_hours, subtasks.\n"
            f"Сообщение пользователя: {message_text}"
        )

    def _plan_with_openrouter(self, message_text: str) -> AssistantPlan:
        previous_http_proxy = os.environ.get("HTTP_PROXY")
        previous_https_proxy = os.environ.get("HTTPS_PROXY")
        previous_http_proxy_lower = os.environ.get("http_proxy")
        previous_https_proxy_lower = os.environ.get("https_proxy")
        if self.settings.openrouter_proxy_url:
            os.environ["HTTP_PROXY"] = self.settings.openrouter_proxy_url
            os.environ["HTTPS_PROXY"] = self.settings.openrouter_proxy_url
            os.environ["http_proxy"] = self.settings.openrouter_proxy_url
            os.environ["https_proxy"] = self.settings.openrouter_proxy_url

        user_prompt = self._build_user_prompt(message_text)
        try:
            try:
                client = self._get_openrouter_client()
                response = self._generate_openrouter_response(client, user_prompt)
                self._log_debug_response(message_text, response)
                payload = self._extract_openrouter_payload(response)
            except Exception as exc:
                raise self._map_openrouter_error(exc) from exc
        finally:
            if self.settings.openrouter_proxy_url:
                if previous_http_proxy is None:
                    os.environ.pop("HTTP_PROXY", None)
                else:
                    os.environ["HTTP_PROXY"] = previous_http_proxy
                if previous_https_proxy is None:
                    os.environ.pop("HTTPS_PROXY", None)
                else:
                    os.environ["HTTPS_PROXY"] = previous_https_proxy
                if previous_http_proxy_lower is None:
                    os.environ.pop("http_proxy", None)
                else:
                    os.environ["http_proxy"] = previous_http_proxy_lower
                if previous_https_proxy_lower is None:
                    os.environ.pop("https_proxy", None)
                else:
                    os.environ["https_proxy"] = previous_https_proxy_lower

        return self._build_plan_from_payload(payload, message_text)

    def _plan_with_gemini(self, message_text: str) -> AssistantPlan:

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise AssistantServiceError(
                "Не могу обработать свободный запрос: не установлен Gemini SDK. "
                "Установите зависимости из requirements.txt и проверьте окружение."
            ) from exc

        previous_http_proxy = os.environ.get("HTTP_PROXY")
        previous_https_proxy = os.environ.get("HTTPS_PROXY")
        previous_http_proxy_lower = os.environ.get("http_proxy")
        previous_https_proxy_lower = os.environ.get("https_proxy")
        if self.settings.gemini_proxy_url:
            os.environ["HTTP_PROXY"] = self.settings.gemini_proxy_url
            os.environ["HTTPS_PROXY"] = self.settings.gemini_proxy_url
            os.environ["http_proxy"] = self.settings.gemini_proxy_url
            os.environ["https_proxy"] = self.settings.gemini_proxy_url
        user_prompt = self._build_user_prompt(message_text)
        try:
            try:
                client = self._get_client(genai)
                try:
                    response = self._generate_structured_response(client, types, user_prompt)
                    self._log_debug_response(message_text, response)
                except Exception as exc:
                    if not self._is_invalid_argument_error(exc):
                        raise
                    logger.warning(
                        "Gemini rejected structured response config for %r; retrying with plain JSON prompt: %s",
                        self._truncate(message_text, 120),
                        exc,
                    )
                    response = self._generate_fallback_response(client, types, user_prompt)
                    self._log_debug_response(f"{message_text} [fallback-after-400]", response)
            except Exception as exc:
                raise self._map_gemini_error(exc) from exc
        finally:
            if self.settings.gemini_proxy_url:
                if previous_http_proxy is None:
                    os.environ.pop("HTTP_PROXY", None)
                else:
                    os.environ["HTTP_PROXY"] = previous_http_proxy
                if previous_https_proxy is None:
                    os.environ.pop("HTTPS_PROXY", None)
                else:
                    os.environ["HTTPS_PROXY"] = previous_https_proxy
                if previous_http_proxy_lower is None:
                    os.environ.pop("http_proxy", None)
                else:
                    os.environ["http_proxy"] = previous_http_proxy_lower
                if previous_https_proxy_lower is None:
                    os.environ.pop("https_proxy", None)
                else:
                    os.environ["https_proxy"] = previous_https_proxy_lower
        try:
            payload = self._extract_payload(response)
        except Exception as exc:
            self._log_debug_parse_failure(message_text, response, exc)
            try:
                retry_response = self._generate_fallback_response(client, types, user_prompt)
                self._log_debug_response(f"{message_text} [fallback]", retry_response)
                payload = self._extract_payload(retry_response)
            except Exception as retry_exc:
                self._log_debug_parse_failure(message_text, locals().get("retry_response"), retry_exc)
                raise AssistantServiceError(
                    "Не могу обработать свободный запрос: Gemini вернула ответ в неожиданном формате. Попробуйте еще раз."
                ) from retry_exc
        return self._build_plan_from_payload(payload, message_text)

    def _build_plan_from_payload(self, payload: dict[str, Any], message_text: str) -> AssistantPlan:
        action = self._string_or_empty(payload.get("action")) or "chat"
        title = self._string_or_empty(payload.get("title"))
        notes = self._string_or_empty(payload.get("notes"))
        task_list = self._string_or_empty(payload.get("task_list"))
        target_title = self._string_or_empty(payload.get("target_title"))
        new_title = self._string_or_empty(payload.get("new_title"))
        raw_subtasks = payload.get("subtasks", []) or []
        subtasks = tuple(
            self._string_or_empty(item)
            for item in raw_subtasks
            if self._string_or_empty(item)
        )
        if action == "create_task":
            task_fields = self._normalize_task_fields(
                message_text=message_text,
                title=title,
                notes=notes,
                task_list=task_list,
                subtasks=subtasks,
            )
            title = task_fields["title"]
            notes = task_fields["notes"]
            task_list = task_fields["task_list"]
            subtasks = task_fields["subtasks"]
        elif action == "update_task":
            task_fields = self._normalize_task_update_fields(
                message_text=message_text,
                title=title,
                notes=notes,
                task_list=task_list,
                target_title=self._string_or_empty(payload.get("target_title")),
                new_title=self._string_or_empty(payload.get("new_title")),
            )
            title = task_fields["title"]
            notes = task_fields["notes"]
            task_list = task_fields["task_list"]
            target_title = task_fields["target_title"]
            new_title = task_fields["new_title"]
        return AssistantPlan(
            action=action,
            reply=self._string_or_empty(payload.get("reply")),
            date=self._string_or_empty(payload.get("date")),
            date_from=self._string_or_empty(payload.get("date_from")),
            date_to=self._string_or_empty(payload.get("date_to")),
            time=self._string_or_empty(payload.get("time")),
            duration_minutes=int(payload.get("duration_minutes", 60) or 60),
            preferred_period=self._string_or_empty(payload.get("preferred_period")),
            excluded_period=self._string_or_empty(payload.get("excluded_period")),
            weekday_filter=self._string_or_empty(payload.get("weekday_filter")),
            within_work_hours=bool(payload.get("within_work_hours", False)),
            title=title,
            target_title=target_title,
            new_title=new_title,
            notes=notes,
            event_group=self._string_or_empty(payload.get("event_group")),
            task_list=task_list,
            outside_work_hours=bool(payload.get("outside_work_hours", False)),
            subtasks=subtasks,
        )

    def _string_or_empty(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _get_gemini_client(self, genai_module):
        if self._client is None or self._client_provider != "gemini":
            self._client = genai_module.Client(api_key=self.settings.gemini_api_key)
            self._client_provider = "gemini"
        return self._client

    def _get_client(self, genai_module):
        return self._get_gemini_client(genai_module)

    def _get_openrouter_client(self):
        if self._client is None or self._client_provider != "openrouter":
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise AssistantServiceError(
                    "Не могу обработать свободный запрос: не установлен OpenAI SDK для OpenRouter. "
                    "Установите зависимости из requirements.txt и проверьте окружение."
                ) from exc

            self._client = OpenAI(
                api_key=self.settings.openrouter_api_key,
                base_url=self.settings.openrouter_base_url,
            )
            self._client_provider = "openrouter"
        return self._client

    def _generate_openrouter_response(self, client, user_prompt: str):
        return client.chat.completions.create(
            model=self.settings.openrouter_model,
            messages=[
                {"role": "system", "content": self._SYSTEM_INSTRUCTION},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=320,
        )

    def _extract_openrouter_payload(self, response) -> dict:
        choices = getattr(response, "choices", None) or []
        for choice in choices:
            message = getattr(choice, "message", None)
            content = getattr(message, "content", "") or ""
            if content:
                return self._extract_json(content)
        raise RuntimeError("LLM вернула ответ не в JSON формате.")

    def _extract_task_notes(self, message_text: str) -> str:
        text = message_text.strip()
        labeled_notes = self._extract_labeled_task_notes(text)
        if labeled_notes:
            return labeled_notes
        patterns = [
            r"(?:^|[\s,.;!?])(?:и\s+)?напиши\s+в\s+(?:заметк[еуи]|описани[еи])\s+(?P<notes>.+)$",
            r"(?:^|[\s,.;!?])(?:и\s+)?добав[ьй]\s+(?:в\s+)?(?:заметк[уыe]|описани[ея])\s+(?P<notes>.+)$",
            r"(?:^|[\s,.;!?])(?:с\s+)?(?:заметк[оаеи]|описани[еем])[:\s]+(?P<notes>.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                notes = match.group("notes").strip(" .,!?:;\n\t")
                if notes:
                    return notes
        return ""

    def _extract_labeled_task_notes(self, text: str) -> str:
        match = re.search(
            r"(?:описани[еия]|заметк[аи])\s*:\s*(?P<notes>.+?)(?=,\s*подзадач[аиы]?\s*:|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return match.group("notes").strip(" \"'.,:;!?")

    def _normalize_task_fields(
        self,
        *,
        message_text: str,
        title: str,
        notes: str,
        task_list: str,
        subtasks: tuple[str, ...],
    ) -> dict[str, Any]:
        normalized_title = title
        normalized_notes = notes or self._extract_task_notes(message_text)
        normalized_task_list = task_list or self._extract_task_list(message_text)
        normalized_subtasks = subtasks or self._extract_task_subtasks(message_text)

        if not title:
            normalized_title = self._extract_task_title(message_text)

        if normalized_title and any(marker in normalized_title.casefold() for marker in ("описан", "подзадач", "в списке")):
            extracted_title = self._extract_task_title(message_text)
            if extracted_title:
                normalized_title = extracted_title

        return {
            "title": normalized_title.strip(),
            "notes": normalized_notes.strip(),
            "task_list": normalized_task_list.strip(),
            "subtasks": tuple(item.strip() for item in normalized_subtasks if item.strip()),
        }

    def _normalize_task_update_fields(
        self,
        *,
        message_text: str,
        title: str,
        notes: str,
        task_list: str,
        target_title: str,
        new_title: str,
    ) -> dict[str, str]:
        normalized_notes = notes or self._extract_task_notes(message_text)
        normalized_task_list = task_list or self._extract_task_list(message_text)
        normalized_target_title = target_title.strip()
        normalized_new_title = new_title.strip()
        normalized_title = title.strip()

        if not normalized_target_title:
            normalized_target_title = self._extract_task_update_target(message_text)

        if normalized_new_title and re.fullmatch(r"списк[еау]\s+.+", normalized_new_title, flags=re.IGNORECASE):
            normalized_new_title = ""

        if not normalized_new_title:
            normalized_new_title = self._extract_task_update_new_title(message_text)

        # For update_task, plain `title` should not silently become a new title when
        # the user only updates notes or due date. Keep it only as a search fallback.
        if not normalized_target_title and normalized_title:
            normalized_target_title = normalized_title

        return {
            "title": normalized_title,
            "notes": normalized_notes.strip(),
            "task_list": normalized_task_list.strip(),
            "target_title": normalized_target_title.strip(),
            "new_title": normalized_new_title.strip(),
        }

    def _extract_task_list(self, message_text: str) -> str:
        match = re.search(
            r"(?:^|[\s,.;!?])в\s+списк[еау]\s+(?P<task_list>[^:,.!?\n]+)",
            message_text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return match.group("task_list").strip(" \"'.,:;!?")

    def _extract_task_subtasks(self, message_text: str) -> tuple[str, ...]:
        match = re.search(
            r"подзадач[аиы]?\s*:\s*(?P<subtasks>.+)$",
            message_text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ()
        raw_items = re.split(r"\s*,\s*", match.group("subtasks").strip())
        return tuple(item.strip(" \"'.,:;!?") for item in raw_items if item.strip(" \"'.,:;!?"))

    def _extract_task_title(self, message_text: str) -> str:
        text = message_text.strip()
        lowered = text.casefold()
        trigger_patterns = [
            r"созда[йт][^\s]*\s+задач[ауи]?",
            r"добав[ьй][^\s]*\s+задач[ауи]?",
        ]
        start_index = 0
        for pattern in trigger_patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if match:
                start_index = match.end()
                break
        candidate = text[start_index:].strip(" :,-")
        candidate = re.sub(r"^в\s+списк[еау]\s+[^:,.!?\n]+\s*:\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.split(r"\s*,\s*(?:описани[еия]|заметк[аи]|подзадач[аиы]?)\s*:", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
        candidate = re.split(r"\s+(?:и\s+)?напиши\s+в\s+(?:заметк[еуи]|описани[еи])\s+", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
        return candidate.strip(" \"'.,:;!?")

    def _extract_task_update_target(self, message_text: str) -> str:
        text = message_text.strip()
        text = re.sub(r"\s+в\s+списк[еау]\s+[^:,.!?\n]+", "", text, flags=re.IGNORECASE)
        match = re.search(
            r"(?:измени|измени|изменить|обнови|обновить|переименуй)\s+задач[ауи]?\s+(?P<title>.+?)(?=,\s*(?:описани[еия]|заметк[аи])\s*:|,\s*срок\b|,\s*дата\b|\s+в\s+|\s*$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return match.group("title").strip(" \"'.,:;!?")

    def _extract_task_update_new_title(self, message_text: str) -> str:
        text = message_text.strip()
        text = re.sub(r"\s+в\s+списк[еау]\s+[^:,.!?\n]+", "", text, flags=re.IGNORECASE)
        match = re.search(
            r"(?:переименуй|измени|изменить)\s+задач[ауи]?\s+.+?\s+в\s+(?P<new_title>.+?)(?=,\s*(?:описани[еия]|заметк[аи])\s*:|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return match.group("new_title").strip(" \"'.,:;!?")

    def reply(self, message_text: str) -> str:
        return self.plan(message_text).reply

    def _map_gemini_error(self, exc: Exception) -> AssistantServiceError:
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            status_code = getattr(exc, "code", None)

        body = getattr(exc, "body", None)
        if body is None:
            body = getattr(exc, "details", None)

        error_payload = body.get("error", body) if isinstance(body, dict) else {}
        error_code = str(error_payload.get("code", "")).strip().casefold()
        error_status = str(
            error_payload.get("status", getattr(exc, "status", ""))
        ).strip().casefold()
        error_message = str(
            error_payload.get("message", getattr(exc, "message", ""))
        ).strip()
        exc_text = " ".join(
            part for part in [str(exc), error_status, error_message] if part
        ).casefold()

        if "user location is not supported for the api use" in exc_text:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: Gemini API отклоняет запрос по геолокации. "
                "Обычный VPN в браузере часто не помогает: проверьте, что Python-процесс идет через "
                "поддерживаемый прокси/сервер, и перезапустите бота."
            )
        if error_code in {"insufficient_quota", "resource_exhausted"} or "quota" in exc_text:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: закончилась квота Gemini API. "
                "Проверьте тариф/биллинг и повторите позже."
            )
        if status_code == 429:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: Gemini API временно ограничивает "
                "частоту запросов. Повторите через минуту."
            )
        if status_code in {400, 404} or error_status == "invalid_argument":
            return AssistantServiceError(
                "Не могу обработать свободный запрос: Gemini API отклонила запрос. "
                "Проверьте GEMINI_MODEL и параметры запроса."
            )
        if status_code in {401, 403} or error_status in {"unauthenticated", "permission_denied"}:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: Gemini API не приняла авторизацию. "
                "Проверьте GEMINI_API_KEY и доступ к модели."
            )
        if status_code and int(status_code) >= 500:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: Gemini API сейчас недоступна. "
                "Попробуйте позже."
            )
        return AssistantServiceError(
            "Не могу обработать свободный запрос из-за ошибки Gemini API. Попробуйте позже."
        )

    def _map_openrouter_error(self, exc: Exception) -> AssistantServiceError:
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            status_code = getattr(exc, "code", None)

        body = getattr(exc, "body", None)
        if body is None:
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    body = response.json()
                except Exception:
                    body = None

        error_payload = body.get("error", body) if isinstance(body, dict) else {}
        error_message = str(error_payload.get("message", getattr(exc, "message", ""))).strip()
        exc_text = " ".join(part for part in (str(exc), error_message) if part).casefold()

        if status_code in {401, 403}:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: OpenRouter не принял авторизацию. "
                "Проверьте OPENROUTER_API_KEY."
            )
        if status_code == 429 or "rate limit" in exc_text:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: OpenRouter временно ограничивает частоту запросов. "
                "Повторите через минуту."
            )
        if status_code in {400, 404}:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: OpenRouter отклонил запрос. "
                "Проверьте OPENROUTER_MODEL и параметры запроса."
            )
        if status_code and int(status_code) >= 500:
            return AssistantServiceError(
                "Не могу обработать свободный запрос: OpenRouter сейчас недоступен. "
                "Попробуйте позже."
            )
        return AssistantServiceError(
            "Не могу обработать свободный запрос из-за ошибки OpenRouter. Попробуйте позже."
        )

    def _is_invalid_argument_error(self, exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            status_code = getattr(exc, "code", None)

        body = getattr(exc, "body", None)
        if body is None:
            body = getattr(exc, "details", None)

        error_payload = body.get("error", body) if isinstance(body, dict) else {}
        error_status = str(
            error_payload.get("status", getattr(exc, "status", ""))
        ).strip().casefold()

        return status_code == 400 or error_status == "invalid_argument"

    def _extract_json(self, raw_text: str) -> dict:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise RuntimeError("LLM вернула ответ не в JSON формате.")
        return json.loads(raw_text[start : end + 1])

    def _extract_payload(self, response) -> dict:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, dict):
            return parsed

        if parsed is not None and hasattr(parsed, "model_dump"):
            dumped = parsed.model_dump()
            if isinstance(dumped, dict):
                return dumped

        raw_text = getattr(response, "text", "") or ""
        if raw_text:
            return self._extract_json(raw_text)

        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", "") or ""
                if part_text:
                    return self._extract_json(part_text)

        raise RuntimeError("LLM вернула ответ не в JSON формате.")

    def _generate_structured_response(self, client, types, user_prompt: str):
        return client.models.generate_content(
            model=self.settings.gemini_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=self._SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=self._RESPONSE_SCHEMA,
                temperature=0.15,
                max_output_tokens=220,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

    def _generate_fallback_response(self, client, types, user_prompt: str):
        fallback_prompt = (
            f"{user_prompt}\n"
            "Ответь только одним JSON-объектом без markdown, без ``` и без пояснений. "
            "Если какое-то поле неизвестно, верни пустую строку, false, 60 или [] по смыслу."
        )
        return client.models.generate_content(
            model=self.settings.gemini_model,
            contents=fallback_prompt,
            config=types.GenerateContentConfig(
                system_instruction=self._SYSTEM_INSTRUCTION,
                temperature=0.1,
                max_output_tokens=260,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

    def _log_debug_response(self, message_text: str, response) -> None:
        if not self.settings.gemini_debug_logging:
            return
        logger.info(
            "Gemini raw response for %r: %s",
            self._truncate(message_text, 120),
            self._summarize_response(response),
        )

    def _log_debug_parse_failure(self, message_text: str, response, exc: Exception) -> None:
        if not self.settings.gemini_debug_logging:
            return
        logger.exception(
            "Gemini response parse failure for %r: %s; response=%s",
            self._truncate(message_text, 120),
            exc,
            self._summarize_response(response),
        )

    def _summarize_response(self, response) -> str:
        parsed = getattr(response, "parsed", None)
        raw_text = getattr(response, "text", "") or ""
        candidate_parts: list[str] = []
        openrouter_choices = getattr(response, "choices", None) or []
        for choice in openrouter_choices:
            message = getattr(choice, "message", None)
            content = getattr(message, "content", "") or ""
            if content:
                candidate_parts.append(self._truncate(content, 300))
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                part_text = getattr(part, "text", "") or ""
                if part_text:
                    candidate_parts.append(self._truncate(part_text, 300))
        return json.dumps(
            {
                "parsed": parsed if isinstance(parsed, dict) else str(parsed) if parsed is not None else None,
                "text": self._truncate(raw_text, 500),
                "candidate_texts": candidate_parts[:3],
            },
            ensure_ascii=False,
            default=str,
        )

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."
