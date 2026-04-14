from __future__ import annotations

import json
from pathlib import Path


COLOR_ALIASES = {
    "lavender": "1",
    "sage": "2",
    "grape": "3",
    "flamingo": "4",
    "banana": "5",
    "tangerine": "6",
    "peacock": "7",
    "graphite": "8",
    "blueberry": "9",
    "basil": "10",
    "tomato": "11",
    "сиреневый": "1",
    "зеленый": "2",
    "фиолетовый": "3",
    "розовый": "4",
    "желтый": "5",
    "оранжевый": "6",
    "бирюзовый": "7",
    "серый": "8",
    "синий": "9",
    "травяной": "10",
    "красный": "11",
}


class EventGroupStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def list_groups(self) -> dict[str, str]:
        return self._read()

    def add_group(self, name: str, color: str) -> tuple[str, str]:
        groups = self._read()
        normalized_name = name.strip()
        color_id = self._normalize_color(color)
        groups[normalized_name] = color_id
        self._write(groups)
        return normalized_name, color_id

    def delete_group(self, name: str) -> bool:
        groups = self._read()
        for key in list(groups):
            if key.casefold() == name.strip().casefold():
                del groups[key]
                self._write(groups)
                return True
        return False

    def resolve_group_name(self, name: str) -> str | None:
        if not name:
            return None
        for group_name in self._read():
            if group_name.casefold() == name.strip().casefold():
                return group_name
        return None

    def resolve_color_id(self, name: str) -> str | None:
        resolved_name = self.resolve_group_name(name)
        if not resolved_name:
            return None
        return self._read().get(resolved_name)

    def _normalize_color(self, raw_color: str) -> str:
        color = raw_color.strip().casefold()
        if color in COLOR_ALIASES:
            return COLOR_ALIASES[color]
        if color.isdigit() and 1 <= int(color) <= 11:
            return color
        raise ValueError("Цвет должен быть id от 1 до 11 или известным названием.")

    def _read(self) -> dict[str, str]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, str]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
