from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import secrets

from fastapi import FastAPI, Header, HTTPException, Request, status
from telegram import Update
from telegram.ext import Application

import bot
from dayflow.config import Settings, load_settings


logger = logging.getLogger(__name__)
TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"


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

        await application.initialize()
        await application.start()
        if web_settings.webhook_base_url:
            webhook_url = f"{web_settings.webhook_base_url}{TELEGRAM_WEBHOOK_PATH}"
            await application.bot.set_webhook(
                url=webhook_url,
                secret_token=web_settings.telegram_webhook_secret,
                allowed_updates=Update.ALL_TYPES,
            )
            logger.info("Telegram webhook configured: %s", webhook_url)
        else:
            logger.warning("WEBHOOK_BASE_URL is empty; Telegram webhook was not configured.")

        yield

        await application.stop()
        await application.shutdown()

    app = FastAPI(title="DayFlow Bot", lifespan=lifespan)
    app.state.telegram_application = application
    app.state.settings = web_settings

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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

    return app


app = create_web_app()
