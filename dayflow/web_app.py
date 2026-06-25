from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import secrets

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from telegram import Update, User
from telegram.ext import Application

import bot
from dayflow.config import Settings, load_settings
from dayflow.cron_service import send_due_digests
from dayflow.ydb_state_store import build_ydb_state_store


logger = logging.getLogger(__name__)
TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"
GOOGLE_OAUTH_CALLBACK_PATH = "/google/oauth/callback"


async def initialize_webhook_application(application: Application, settings: Settings) -> None:
    """Initialize enough of PTB to process webhook updates without blocking on getMe."""
    bot_id = int(application.bot.token.split(":", 1)[0])
    application.bot._bot_user = User(
        id=bot_id,
        first_name=settings.telegram_bot_username or "DayFlow",
        is_bot=True,
        username=settings.telegram_bot_username or None,
    )
    await asyncio.gather(
        application.bot._request[0].initialize(),
        application.bot._request[1].initialize(),
    )
    application.bot._initialized = True
    await application.update_processor.initialize()
    application._initialized = True


def create_web_app(
    *,
    settings: Settings | None = None,
    telegram_application: Application | None = None,
) -> FastAPI:
    web_settings = settings or load_settings()
    application = telegram_application or bot.build_application()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not web_settings.telegram_webhook_secret:
            raise RuntimeError("TELEGRAM_WEBHOOK_SECRET must be configured in webhook mode.")

        await initialize_webhook_application(application, web_settings)
        if web_settings.webhook_base_url:
            webhook_url = f"{web_settings.webhook_base_url}{TELEGRAM_WEBHOOK_PATH}"
            try:
                await application.bot.set_webhook(
                    url=webhook_url,
                    secret_token=web_settings.telegram_webhook_secret,
                    allowed_updates=Update.ALL_TYPES,
                    max_connections=1,
                )
                logger.info("Telegram webhook configured: %s", webhook_url)
            except Exception:
                logger.exception("Failed to configure Telegram webhook; keeping the existing webhook.")
        else:
            logger.warning("WEBHOOK_BASE_URL is empty; Telegram webhook was not configured.")
        try:
            bot.google_auth_session_store.cleanup_expired()
        except Exception:
            logger.exception("Failed to clean expired Google OAuth sessions")

        yield

        await application.shutdown()

    app = FastAPI(title="DayFlow Bot", lifespan=lifespan)
    app.state.telegram_application = application
    app.state.settings = web_settings

    @app.get("/health")
    async def health() -> dict[str, str]:
        if web_settings.storage_backend == "ydb":
            try:
                await asyncio.to_thread(build_ydb_state_store(web_settings).ping)
            except Exception as exc:
                logger.exception("YDB health check failed")
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="YDB unavailable") from exc
        return {"status": "ok", "storage": web_settings.storage_backend}

    @app.post(TELEGRAM_WEBHOOK_PATH)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        if not secrets.compare_digest(
            x_telegram_bot_api_secret_token or "",
            web_settings.telegram_webhook_secret,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook secret.")

        payload = await request.json()
        update = Update.de_json(payload, application.bot)
        await application.process_update(update)
        return {"ok": True}

    @app.get(GOOGLE_OAUTH_CALLBACK_PATH, response_class=HTMLResponse)
    async def google_oauth_callback(request: Request, state: str = "") -> str:
        stored = bot.google_auth_session_store.get_by_state(state)
        if not stored:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth session is missing or expired.")
        user_id, session = stored
        try:
            await asyncio.to_thread(
                bot.complete_google_auth,
                web_settings,
                user_id,
                session,
                str(request.url),
            )
            bot.google_auth_session_store.delete(user_id)
            bot.pending_google_auth.pop(user_id, None)
            bot.reset_user_google_services(user_id)
            profile = bot.user_profile_store.get(user_id)
            await application.bot.send_message(
                chat_id=profile.chat_id if profile else user_id,
                text="Google-аккаунт подключен. Можно вернуться в Telegram.",
            )
        except Exception as exc:
            logger.exception("Google OAuth callback failed for user_id=%s", user_id)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to complete Google OAuth.") from exc
        return "<html><body><h1>Google подключен</h1><p>Можно закрыть эту страницу и вернуться в Telegram.</p></body></html>"

    async def run_digest(kind: str, secret: str | None) -> dict:
        if not web_settings.cron_secret or not secrets.compare_digest(secret or "", web_settings.cron_secret):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid cron secret.")
        result = await asyncio.to_thread(send_due_digests, web_settings, kind)
        logger.info("Cron digest kind=%s result=%s", kind, result)
        return {
            "kind": kind,
            "due": result.due,
            "sent": result.sent,
            "skipped": result.skipped,
            "failed": result.failed,
        }

    @app.post("/cron/morning-digest")
    async def morning_digest(x_cron_secret: str | None = Header(default=None)) -> dict:
        return await run_digest("morning", x_cron_secret)

    @app.post("/cron/evening-digest")
    async def evening_digest(x_cron_secret: str | None = Header(default=None)) -> dict:
        return await run_digest("evening", x_cron_secret)

    return app


app = create_web_app()
