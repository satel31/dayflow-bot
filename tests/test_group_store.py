from __future__ import annotations

import pytest

from dayflow.group_store import COLOR_ALIASES, EventGroupStore


def make_store(initial=None):
    state = dict(initial or {})
    store = EventGroupStore.__new__(EventGroupStore)

    def _read():
        return dict(state)

    def _write(data):
        state.clear()
        state.update(data)

    store._read = _read
    store._write = _write
    return store, state


def test_group_store_add_resolve_and_delete():
    store, state = make_store()

    name, color_id = store.add_group("Работа", "синий")

    assert (name, color_id) == ("Работа", COLOR_ALIASES["синий"])
    assert store.resolve_group_name("работа") == "Работа"
    assert store.resolve_color_id("РАБОТА") == "9"
    assert store.delete_group("работа") is True
    assert state == {}


def test_group_store_rejects_unknown_color():
    store, _ = make_store()

    with pytest.raises(ValueError, match="Цвет должен быть id от 1 до 11"):
        store.add_group("Личное", "99")
