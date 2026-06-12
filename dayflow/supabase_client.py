from __future__ import annotations

from typing import Any

import httpx

from dayflow.config import Settings


class SupabaseConfigurationError(RuntimeError):
    pass


class SupabaseRestClient:
    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not url or not service_role_key:
            raise SupabaseConfigurationError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured."
            )
        self._client = httpx.Client(
            base_url=f"{url.rstrip('/')}/rest/v1",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
            timeout=20.0,
            transport=transport,
        )

    def select(
        self,
        table: str,
        *,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        response = self._client.get(f"/{table}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"Supabase table {table} returned a non-list response.")
        return payload

    def upsert(self, table: str, payload: dict[str, Any], *, on_conflict: str) -> None:
        response = self._client.post(
            f"/{table}",
            params={"on_conflict": on_conflict},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
        )
        response.raise_for_status()

    def delete(self, table: str, *, params: dict[str, str]) -> bool:
        response = self._client.delete(
            f"/{table}",
            params=params,
            headers={"Prefer": "return=representation"},
        )
        response.raise_for_status()
        payload = response.json()
        return isinstance(payload, list) and bool(payload)

    def get_app_state(self, key: str) -> Any | None:
        rows = self.select(
            "app_state",
            params={"select": "value", "key": f"eq.{key}", "limit": "1"},
        )
        return rows[0]["value"] if rows else None

    def set_app_state(self, key: str, value: Any) -> None:
        self.upsert("app_state", {"key": key, "value": value}, on_conflict="key")


def build_supabase_client(settings: Settings) -> SupabaseRestClient:
    return SupabaseRestClient(settings.supabase_url, settings.supabase_service_role_key)
