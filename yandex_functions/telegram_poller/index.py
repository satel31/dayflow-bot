from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import ydb


TABLE_NAME = "dayflow_state"
QUEUE_NAMESPACE = "telegram_update_queue"
STATE_NAMESPACE = "telegram_poller"
OFFSET_KEY = "offset"

_driver = None
_pool = None


def handler(event, context):
    print("telegram_poller: started", flush=True)
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

        offset = int(_get_state(STATE_NAMESPACE, OFFSET_KEY) or 0)
        print(f"telegram_poller: requesting updates offset={offset}", flush=True)
        updates = _get_updates(token, offset)

        next_offset = offset
        for update in updates:
            _enqueue(update)
            next_offset = max(next_offset, int(update["update_id"]) + 1)

        if next_offset != offset:
            _set_state(STATE_NAMESPACE, OFFSET_KEY, next_offset)

        print(
            f"telegram_poller: completed received={len(updates)} next_offset={next_offset}",
            flush=True,
        )
        return {
            "statusCode": 200,
            "body": json.dumps(
                {"ok": True, "received": len(updates), "next_offset": next_offset}
            ),
        }
    except Exception as exc:
        print(f"telegram_poller: failed: {type(exc).__name__}: {exc}", flush=True)
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
            ),
        }


def _get_updates(token: str, offset: int) -> list[dict[str, Any]]:
    params = {
        "offset": str(offset),
        "limit": "100",
        "timeout": "0",
        "allowed_updates": json.dumps(
            ["message", "edited_message", "callback_query", "my_chat_member"]
        ),
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        data=data,
        method="POST",
    )

    last_error = None
    for attempt, delay in enumerate((0, 1, 3), start=1):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram getUpdates failed: {payload}")
            return payload.get("result") or []
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            print(f"telegram_poller: getUpdates attempt={attempt} failed: {exc}", flush=True)
    raise RuntimeError(f"Telegram getUpdates failed after retries: {last_error}")


def _enqueue(payload: dict[str, Any]) -> None:
    key = str(payload["update_id"])
    value = {
        "payload": payload,
        "attempts": 0,
        "created_at": int(time.time()),
        "last_error": "",
    }
    _set_state(QUEUE_NAMESPACE, key, value)


def _get_state(namespace: str, key: str):
    query = (
        "DECLARE $namespace AS Utf8; "
        "DECLARE $key AS Utf8; "
        f"SELECT value FROM `{TABLE_NAME}` "
        "WHERE namespace = $namespace AND key = $key;"
    )
    rows = _execute(query, {"$namespace": namespace, "$key": key})
    return json.loads(rows[0]["value"]) if rows else None


def _set_state(namespace: str, key: str, value: Any) -> None:
    query = (
        "DECLARE $namespace AS Utf8; "
        "DECLARE $key AS Utf8; "
        "DECLARE $value AS Utf8; "
        f"UPSERT INTO `{TABLE_NAME}` (namespace, key, value) "
        "VALUES ($namespace, $key, $value);"
    )
    _execute(
        query,
        {
            "$namespace": namespace,
            "$key": key,
            "$value": json.dumps(value, ensure_ascii=True),
        },
    )


def _execute(query: str, params: dict[str, Any]) -> list:
    pool = _pool_instance()

    def operation(session):
        prepared_query = session.prepare(query)
        result_sets = session.transaction().execute(
            prepared_query,
            parameters=params,
            commit_tx=True,
        )
        return list(result_sets[0].rows) if result_sets else []

    return pool.retry_operation_sync(operation)


def _pool_instance():
    global _driver, _pool
    if _pool is None:
        endpoint = os.environ.get("YDB_ENDPOINT", "")
        database = os.environ.get("YDB_DATABASE", "")
        if not endpoint or not database:
            raise RuntimeError("YDB_ENDPOINT and YDB_DATABASE must be configured")
        _driver = ydb.Driver(
            endpoint=endpoint,
            database=database,
            credentials=ydb.iam.MetadataUrlCredentials(),
        )
        _driver.wait(timeout=3, fail_fast=True)
        _pool = ydb.SessionPool(_driver)
    return _pool
