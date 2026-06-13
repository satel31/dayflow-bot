from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import ydb

from dayflow.config import Settings


TABLE_NAME = "dayflow_state"


class YdbStateStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.ydb_endpoint or not settings.ydb_database:
            raise RuntimeError("YDB_ENDPOINT and YDB_DATABASE must be configured.")
        credentials = _build_credentials(settings.ydb_service_account_key_json)
        self.driver = ydb.Driver(
            endpoint=settings.ydb_endpoint,
            database=settings.ydb_database,
            credentials=credentials,
        )
        self.driver.wait(timeout=10)
        self.pool = ydb.SessionPool(self.driver)
        self._ensure_table()

    def get(self, namespace: str, key: str) -> Any | None:
        rows = self._execute(
            f"""
            DECLARE $namespace AS Utf8;
            DECLARE $key AS Utf8;
            SELECT value FROM `{TABLE_NAME}`
            WHERE namespace = $namespace AND key = $key;
            """,
            {"$namespace": namespace, "$key": key},
        )
        return json.loads(rows[0]["value"]) if rows else None

    def list(self, namespace: str) -> dict[str, Any]:
        rows = self._execute(
            f"""
            DECLARE $namespace AS Utf8;
            SELECT key, value FROM `{TABLE_NAME}` WHERE namespace = $namespace;
            """,
            {"$namespace": namespace},
        )
        return {str(row["key"]): json.loads(row["value"]) for row in rows}

    def set(self, namespace: str, key: str, value: Any) -> None:
        self._execute(
            f"""
            DECLARE $namespace AS Utf8;
            DECLARE $key AS Utf8;
            DECLARE $value AS Utf8;
            UPSERT INTO `{TABLE_NAME}` (namespace, key, value)
            VALUES ($namespace, $key, $value);
            """,
            {"$namespace": namespace, "$key": key, "$value": json.dumps(value, ensure_ascii=True)},
        )

    def delete(self, namespace: str, key: str) -> bool:
        existed = self.get(namespace, key) is not None
        self._execute(
            f"""
            DECLARE $namespace AS Utf8;
            DECLARE $key AS Utf8;
            DELETE FROM `{TABLE_NAME}` WHERE namespace = $namespace AND key = $key;
            """,
            {"$namespace": namespace, "$key": key},
        )
        return existed

    def ping(self) -> None:
        self._execute(f"SELECT COUNT(*) AS count FROM `{TABLE_NAME}`;", {})

    def _ensure_table(self) -> None:
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

        self.pool.retry_operation_sync(operation)

    def _execute(self, query: str, params: dict) -> list:
        def operation(session):
            result_sets = session.transaction().execute(query, params, commit_tx=True)
            return list(result_sets[0].rows) if result_sets else []

        return self.pool.retry_operation_sync(operation)


@lru_cache(maxsize=4)
def build_ydb_state_store(settings: Settings) -> YdbStateStore:
    return YdbStateStore(settings)


def _build_credentials(raw_json: str):
    if not raw_json:
        return ydb.iam.MetadataUrlCredentials()
    payload = json.loads(raw_json)
    return ydb.iam.ServiceAccountCredentials(
        service_account_id=payload["service_account_id"],
        access_key_id=payload["id"],
        private_key=payload["private_key"],
    )
