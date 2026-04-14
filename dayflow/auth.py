from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from dayflow.config import Settings


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
    token_path = _resolve_token_path(settings, user_id)
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
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
    credentials_path = Path(settings.google_credentials_path)
    if not credentials_path.exists():
        raise RuntimeError(
            "Не найден credentials.json. Скачайте OAuth client credentials из Google Cloud Console."
        )

    redirect_uri = f"http://localhost:{random.randint(49152, 65535)}/"
    flow = Flow.from_client_secrets_file(
        str(credentials_path),
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
    credentials_path = Path(settings.google_credentials_path)
    flow = Flow.from_client_secrets_file(
        str(credentials_path),
        scopes=GOOGLE_SCOPES,
        state=session.state,
        redirect_uri=session.redirect_uri,
        code_verifier=session.code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.fetch_token(authorization_response=authorization_response.replace("http://", "https://", 1))
    creds = flow.credentials
    token_path = token_path_for_user(settings, user_id)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def disconnect_google_account(settings: Settings, user_id: int) -> bool:
    token_path = token_path_for_user(settings, user_id)
    if not token_path.exists():
        return False
    token_path.unlink()
    return True


def _resolve_token_path(settings: Settings, user_id: int | None) -> Path:
    if user_id is None:
        return Path(settings.google_token_path)
    return token_path_for_user(settings, user_id)
