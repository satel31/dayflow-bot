from __future__ import annotations

from dayflow.auth import GoogleAuthSession
from dayflow.config import Settings
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


class SupabaseGoogleAuthSessionStore:
    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    def save(self, user_id: int, session: GoogleAuthSession) -> None:
        self.client.upsert(
            "google_auth_sessions",
            {
                "user_id": int(user_id),
                "state": session.state,
                "redirect_uri": session.redirect_uri,
                "code_verifier": session.code_verifier,
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
        return _session_from_row(rows[0]) if rows else None

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
        return int(rows[0]["user_id"]), _session_from_row(rows[0])

    def delete(self, user_id: int) -> bool:
        return self.client.delete("google_auth_sessions", params={"user_id": f"eq.{int(user_id)}"})


def build_google_auth_session_store(settings: Settings):
    if settings.persistent_backend == "supabase":
        return SupabaseGoogleAuthSessionStore(build_supabase_client(settings))
    if settings.persistent_backend != "file":
        raise ValueError(f"Unsupported PERSISTENT_BACKEND: {settings.persistent_backend}")
    return InMemoryGoogleAuthSessionStore()


def _session_from_row(row: dict) -> GoogleAuthSession:
    return GoogleAuthSession(
        auth_url="",
        state=str(row["state"]),
        redirect_uri=str(row["redirect_uri"]),
        code_verifier=str(row["code_verifier"]),
    )
