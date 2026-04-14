from __future__ import annotations

from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_timezone(timezone_name: str) -> tzinfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        if timezone_name == "Europe/Moscow":
            return timezone(timedelta(hours=3), name=timezone_name)
        raise RuntimeError(
            "Не удалось загрузить таймзону "
            f'"{timezone_name}". Установите зависимости из requirements.txt '
            "(включая tzdata) или укажите корректный IANA TIMEZONE, например Europe/Moscow."
        ) from exc
