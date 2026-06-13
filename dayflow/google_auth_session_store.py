from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from dayflow.auth import GoogleAuthSession
from dayflow.config import Settings
from dayflow.crypto import decrypt_text, encrypt_text
from dayflow.ydb_state_store import build_ydb_state_store


class GoogleAuthSessionStore:
    def __init__(self, path: str, encryption_key: str = "") -> None:
        self.path = Path(path)
        self.encryption_key = encryption_key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def save(self, user_id: int, session: GoogleAuthSession) -> None:
        self.delete(user_id)
        rows = self._read()
        rows.append(
            {
                "user_id": int(user_id),
                "state": session.state,
                "redirect_uri": session.redirect_uri,
                "code_verifier": encrypt_text(session.code_verifier, self.encryption_key),
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            }
        )
        self._write(rows)

    def get_for_user(self, user_id: int) -> GoogleAuthSession | None:
        row = next((item for item in self._valid_rows() if int(item["user_id"]) == int(user_id)), None)
        return self._session(row) if row else None

    def get_by_state(self, state: str) -> tuple[int, GoogleAuthSession] | None:
        row = next((item for item in self._valid_rows() if item["state"] == state), None)
        return (int(row["user_id"]), self._session(row)) if row else None

    def delete(self, user_id: int) -> bool:
        rows = self._read()
        filtered = [item for item in rows if int(item["user_id"]) != int(user_id)]
        if len(filtered) == len(rows):
            return False
        self._write(filtered)
        return True

    def cleanup_expired(self) -> int:
        rows = self._read()
        valid = self._valid_rows(rows)
        if len(valid) != len(rows):
            self._write(valid)
        return len(rows) - len(valid)

    def _valid_rows(self, rows: list[dict] | None = None) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [
            item
            for item in (rows if rows is not None else self._read())
            if datetime.fromisoformat(item["expires_at"]) > now
        ]

    def _session(self, row: dict) -> GoogleAuthSession:
        return GoogleAuthSession(
            auth_url="",
            state=row["state"],
            redirect_uri=row["redirect_uri"],
            code_verifier=decrypt_text(row["code_verifier"], self.encryption_key),
        )

    def _read(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: list[dict]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def build_google_auth_session_store(settings: Settings) -> GoogleAuthSessionStore:
    if settings.storage_backend == "ydb":
        return YdbGoogleAuthSessionStore(build_ydb_state_store(settings), settings.data_encryption_key)
    return GoogleAuthSessionStore(settings.google_auth_sessions_path, settings.data_encryption_key)


class YdbGoogleAuthSessionStore:
    def __init__(self, state, encryption_key: str = "") -> None:
        self.state = state
        self.encryption_key = encryption_key

    def save(self, user_id: int, session: GoogleAuthSession) -> None:
        self.state.set(
            "google_auth_sessions",
            str(user_id),
            {
                "state": session.state,
                "redirect_uri": session.redirect_uri,
                "code_verifier": encrypt_text(session.code_verifier, self.encryption_key),
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            },
        )

    def get_for_user(self, user_id: int) -> GoogleAuthSession | None:
        payload = self.state.get("google_auth_sessions", str(user_id))
        return self._session(payload) if payload and self._valid(payload) else None

    def get_by_state(self, state: str) -> tuple[int, GoogleAuthSession] | None:
        for user_id, payload in self.state.list("google_auth_sessions").items():
            if payload["state"] == state and self._valid(payload):
                return int(user_id), self._session(payload)
        return None

    def delete(self, user_id: int) -> bool:
        return self.state.delete("google_auth_sessions", str(user_id))

    def cleanup_expired(self) -> int:
        expired = [
            user_id
            for user_id, payload in self.state.list("google_auth_sessions").items()
            if not self._valid(payload)
        ]
        for user_id in expired:
            self.state.delete("google_auth_sessions", user_id)
        return len(expired)

    def _valid(self, payload: dict) -> bool:
        return datetime.fromisoformat(payload["expires_at"]) > datetime.now(timezone.utc)

    def _session(self, payload: dict) -> GoogleAuthSession:
        return GoogleAuthSession(
            auth_url="",
            state=payload["state"],
            redirect_uri=payload["redirect_uri"],
            code_verifier=decrypt_text(payload["code_verifier"], self.encryption_key),
        )
