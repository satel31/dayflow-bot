from __future__ import annotations

from dayflow.auth import GoogleAuthSession
from dayflow.config import Settings
from dayflow.crypto import decrypt_text, encrypt_text
from dayflow.supabase_client import SupabaseRestClient, build_supabase_client


class InMemoryGoogleAuthSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[int, GoogleAuthSession] = {}

    def save(self, user_id: int, session: GoogleAuthSession) -> None:
        self.sessions[int(user_id)] = session

    def get_for_user(self, user_id: int) -> GoogleAuthSession | None:
        return self.sessions.get(int(user_id))

    def get_by_state(self, state: str) -> tuple[int, GoogleAuthSession] | None:
        for user_id, session in self.sessions.items():
            if session.state == state:
                return user_id, session
        return None

    def delete(self, user_id: int) -> bool:
        return self.sessions.pop(int(user_id), None) is not None

    def cleanup_expired(self) -> int:
        return 0


class SupabaseGoogleAuthSessionStore:
    def __init__(self, client: SupabaseRestClient, encryption_key: str = "") -> None:
        self.client = client
        self.encryption_key = encryption_key

    def save(self, user_id: int, session: GoogleAuthSession) -> None:
        self.delete(user_id)
        self.client.upsert(
            "google_auth_sessions",
            {
                "user_id": int(user_id),
                "state": session.state,
                "redirect_uri": session.redirect_uri,
                "code_verifier": encrypt_text(session.code_verifier, self.encryption_key),
            },
            on_conflict="user_id",
        )

    def get_for_user(self, user_id: int) -> GoogleAuthSession | None:
        rows = self.client.select(
            "google_auth_sessions",
            params={
                "select": "state,redirect_uri,code_verifier",
                "user_id": f"eq.{int(user_id)}",
                "limit": "1",
            },
        )
        return _session_from_row(rows[0], self.encryption_key) if rows else None

    def get_by_state(self, state: str) -> tuple[int, GoogleAuthSession] | None:
        rows = self.client.select(
            "google_auth_sessions",
            params={
                "select": "user_id,state,redirect_uri,code_verifier",
                "state": f"eq.{state}",
                "limit": "1",
            },
        )
        if not rows:
            return None
        return int(rows[0]["user_id"]), _session_from_row(rows[0], self.encryption_key)

    def delete(self, user_id: int) -> bool:
        return self.client.delete("google_auth_sessions", params={"user_id": f"eq.{int(user_id)}"})

    def cleanup_expired(self) -> int:
        rows = self.client.select(
            "google_auth_sessions",
            params={"select": "user_id", "expires_at": "lt.now()"},
        )
        for row in rows:
            self.delete(int(row["user_id"]))
        return len(rows)


def build_google_auth_session_store(settings: Settings):
    if settings.persistent_backend == "supabase":
        return SupabaseGoogleAuthSessionStore(build_supabase_client(settings), settings.data_encryption_key)
    if settings.persistent_backend != "file":
        raise ValueError(f"Unsupported PERSISTENT_BACKEND: {settings.persistent_backend}")
    return InMemoryGoogleAuthSessionStore()


def _session_from_row(row: dict, encryption_key: str = "") -> GoogleAuthSession:
    return GoogleAuthSession(
        auth_url="",
        state=str(row["state"]),
        redirect_uri=str(row["redirect_uri"]),
        code_verifier=decrypt_text(str(row["code_verifier"]), encryption_key),
    )
