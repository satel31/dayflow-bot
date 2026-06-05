from __future__ import annotations

import asyncio

from telegram import Bot
from telegram.request import HTTPXRequest

from dayflow.config import Settings


def send_telegram_message(settings: Settings, chat_id: int, text: str) -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN.")
    asyncio.run(_send_telegram_message(settings, chat_id, text))


async def _send_telegram_message(settings: Settings, chat_id: int, text: str) -> None:
    request = HTTPXRequest(proxy=settings.telegram_proxy_url) if settings.telegram_proxy_url else None
    bot = Bot(token=settings.telegram_bot_token, request=request)
    async with bot:
        await bot.send_message(chat_id=chat_id, text=text)
