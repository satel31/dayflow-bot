from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.request import HTTPXRequest

from dayflow.config import Settings


logger = logging.getLogger(__name__)


def send_telegram_message(settings: Settings, chat_id: int, text: str) -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN.")
    asyncio.run(_send_telegram_message(settings, chat_id, text))


async def _send_telegram_message(settings: Settings, chat_id: int, text: str) -> None:
    request = HTTPXRequest(proxy=settings.telegram_proxy_url) if settings.telegram_proxy_url else None
    bot = Bot(token=settings.telegram_bot_token, request=request)
    async with bot:
        await send_with_retry(bot.send_message, chat_id=chat_id, text=text)


async def send_with_retry(call, **kwargs):
    delays = (1.0, 3.0, 7.0)
    last_exc = None
    for attempt in range(len(delays) + 1):
        try:
            return await call(**kwargs)
        except RetryAfter as exc:
            last_exc = exc
            if attempt == len(delays):
                break
            await asyncio.sleep(max(float(exc.retry_after), delays[attempt]))
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            if attempt == len(delays):
                break
            await asyncio.sleep(delays[attempt])
    logger.exception("Telegram request failed after retries", exc_info=last_exc)
    raise last_exc
