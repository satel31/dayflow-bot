from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from dayflow.config import Settings
from dayflow.crypto import decrypt_text, encrypt_text
from dayflow.ydb_state_store import build_ydb_state_store


GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]


class GoogleAuthRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleAuthSession:
    auth_url: str
    state: str
    redirect_uri: str
    code_verifier: str


def token_path_for_user(settings: Settings, user_id: int) -> Path:
    token_root = Path(settings.google_tokens_dir)
    return token_root / f"{user_id}.json"


def load_google_credentials(settings: Settings, user_id: int | None = None) -> Credentials:
    credentials_path = Path(settings.google_credentials_path)
    token_json = _read_token_json(settings, user_id)
    creds = Credentials.from_authorized_user_info(json.loads(token_json), GOOGLE_SCOPES) if token_json else None

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _write_token_json(settings, user_id, creds.to_json())
        return creds

    if creds and creds.valid:
        return creds

    if user_id is not None:
        raise GoogleAuthRequiredError(
            "Google Calendar и Google Tasks еще не подключены для этого пользователя. "
            "Отправьте /connect_google."
        )

    if not credentials_path.exists():
        raise RuntimeError(
            "Не найден credentials.json. Скачайте OAuth client credentials из Google Cloud Console."
        )

    raise GoogleAuthRequiredError(
        "Не найден token.json. Для многопользовательского режима используйте /connect_google."
    )


def build_google_auth_session(settings: Settings, user_id: int) -> GoogleAuthSession:
    redirect_uri = (
        f"{settings.webhook_base_url}/google/oauth/callback"
        if settings.webhook_base_url
        else f"http://localhost:{random.randint(49152, 65535)}/"
    )
    flow = Flow.from_client_config(
        _load_google_client_config(settings),
        scopes=GOOGLE_SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=True,
    )
    auth_url, state = flow.authorization_url(prompt="consent", include_granted_scopes="true")
    if not flow.code_verifier:
        raise RuntimeError("Не удалось подготовить OAuth PKCE verifier.")
    return GoogleAuthSession(
        auth_url=auth_url,
        state=state,
        redirect_uri=redirect_uri,
        code_verifier=flow.code_verifier,
    )


def complete_google_auth(
    settings: Settings,
    user_id: int,
    session: GoogleAuthSession,
    authorization_response: str,
) -> Credentials:
    flow = Flow.from_client_config(
        _load_google_client_config(settings),
        scopes=GOOGLE_SCOPES,
        state=session.state,
        redirect_uri=session.redirect_uri,
        code_verifier=session.code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.fetch_token(authorization_response=authorization_response.replace("http://", "https://", 1))
    creds = flow.credentials
    _write_token_json(settings, user_id, creds.to_json())
    return creds


def disconnect_google_account(settings: Settings, user_id: int) -> bool:
    if settings.storage_backend == "ydb":
        return build_ydb_state_store(settings).delete("google_tokens", str(user_id))
    token_path = token_path_for_user(settings, user_id)
    if not token_path.exists():
        return False
    token_path.unlink()
    return True


def google_token_exists(settings: Settings, user_id: int) -> bool:
    if settings.storage_backend == "ydb":
        return build_ydb_state_store(settings).get("google_tokens", str(user_id)) is not None
    return token_path_for_user(settings, user_id).exists()


def _resolve_token_path(settings: Settings, user_id: int | None) -> Path:
    if user_id is None:
        return Path(settings.google_token_path)
    return token_path_for_user(settings, user_id)


def _read_token_json(settings: Settings, user_id: int | None) -> str | None:
    if settings.storage_backend == "ydb" and user_id is not None:
        value = build_ydb_state_store(settings).get("google_tokens", str(user_id))
        return decrypt_text(str(value), settings.data_encryption_key) if value else None
    token_path = _resolve_token_path(settings, user_id)
    if not token_path.exists():
        return None
    return decrypt_text(token_path.read_text(encoding="utf-8"), settings.data_encryption_key)


def _write_token_json(settings: Settings, user_id: int | None, token_json: str) -> None:
    if settings.storage_backend == "ydb":
        if user_id is None:
            raise ValueError("YDB token storage requires a Telegram user_id.")
        build_ydb_state_store(settings).set(
            "google_tokens",
            str(user_id),
            encrypt_text(token_json, settings.data_encryption_key),
        )
        return
    token_path = _resolve_token_path(settings, user_id)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(encrypt_text(token_json, settings.data_encryption_key), encoding="utf-8")


def _load_google_client_config(settings: Settings) -> dict:
    if settings.google_credentials_json:
        return json.loads(settings.google_credentials_json)
    credentials_path = Path(settings.google_credentials_path)
    if credentials_path.exists():
        return json.loads(credentials_path.read_text(encoding="utf-8"))
    raise RuntimeError(
        "Не настроены GOOGLE_CREDENTIALS_JSON или GOOGLE_CREDENTIALS_PATH."
    )
