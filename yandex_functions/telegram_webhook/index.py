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


def handler(event, context):
    print("telegram_webhook: handler started", flush=True)
    try:
        return _handle(event)
    except Exception as exc:
        print(f"telegram_webhook: failed: {type(exc).__name__}: {exc}", flush=True)
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

    print("telegram_webhook: secret accepted", flush=True)
    payload = json.loads(_body(event))
    print(f"telegram_webhook: enqueue started update_id={payload.get('update_id')}", flush=True)
    key = _enqueue(payload)
    print(f"telegram_webhook: enqueue completed key={key}", flush=True)
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

    print("telegram_webhook: YDB execute started", flush=True)
    result = pool.retry_operation_sync(operation)
    print("telegram_webhook: YDB execute completed", flush=True)
    return result


def _pool_instance():
    global _driver, _pool
    if _pool is None:
        endpoint = os.environ.get("YDB_ENDPOINT", "")
        database = os.environ.get("YDB_DATABASE", "")
        if not endpoint or not database:
            raise RuntimeError("YDB_ENDPOINT and YDB_DATABASE must be configured")
        print("telegram_webhook: YDB driver creating", flush=True)
        _driver = ydb.Driver(
            endpoint=endpoint,
            database=database,
            credentials=ydb.iam.MetadataUrlCredentials(),
        )
        print("telegram_webhook: YDB driver waiting", flush=True)
        _driver.wait(timeout=3, fail_fast=True)
        print("telegram_webhook: YDB driver ready", flush=True)
        _pool = ydb.SessionPool(_driver)
    return _pool


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
