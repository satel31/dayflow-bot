from __future__ import annotations

import base64
import json
import os
import secrets
from time import time
from typing import Any

import ydb


TABLE_NAME = "dayflow_state"
QUEUE_NAMESPACE = "telegram_update_queue"

_driver = None
_pool = None
_table_ready = False


def handler(event, context):
    try:
        return _handle(event)
    except Exception as exc:
        return _response(
            500,
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )


def _handle(event: dict[str, Any]):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    if method and method.upper() != "POST":
        return _response(405, {"ok": False, "error": "Method not allowed"})

    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected_secret:
        return _response(500, {"ok": False, "error": "TELEGRAM_WEBHOOK_SECRET is missing"})

    headers = _lower_headers(event.get("headers") or {})
    actual_secret = headers.get("x-telegram-bot-api-secret-token", "")
    if not secrets.compare_digest(actual_secret, expected_secret):
        return _response(403, {"ok": False, "error": "Invalid webhook secret"})

    payload = json.loads(_body(event))
    key = _enqueue(payload)
    return _response(200, {"ok": True, "queued": key})


def _enqueue(payload: dict[str, Any]) -> str:
    update_id = payload.get("update_id")
    key = str(update_id) if update_id is not None else f"unknown:{int(time() * 1000)}"
    value = {
        "payload": payload,
        "attempts": 0,
        "created_at": int(time()),
        "last_error": "",
    }
    _execute(
        f"""
        DECLARE $namespace AS Utf8;
        DECLARE $key AS Utf8;
        DECLARE $value AS Utf8;
        UPSERT INTO `{TABLE_NAME}` (namespace, key, value)
        VALUES ($namespace, $key, $value);
        """,
        {
            "$namespace": QUEUE_NAMESPACE,
            "$key": key,
            "$value": json.dumps(value, ensure_ascii=True),
        },
    )
    return key


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
    global _driver, _pool, _table_ready
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
        _driver.wait(timeout=10)
        _pool = ydb.SessionPool(_driver)
    if not _table_ready:
        _ensure_table()
        _table_ready = True
    return _pool


def _ensure_table() -> None:
    def operation(session):
        session.execute_scheme(
            f"""
            CREATE TABLE IF NOT EXISTS `{TABLE_NAME}` (
                namespace Utf8 NOT NULL,
                key Utf8 NOT NULL,
                value Utf8 NOT NULL,
                PRIMARY KEY (namespace, key)
            );
            """
        )

    _pool.retry_operation_sync(operation)


def _body(event: dict[str, Any]) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body).decode("utf-8")
    return body


def _lower_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }
