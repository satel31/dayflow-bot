# Telegram webhook Yandex Function

Entrypoint:

```text
index.handler
```

Runtime:

```text
Python 3.12
```

Environment variables:

```text
TELEGRAM_WEBHOOK_SECRET
YDB_ENDPOINT
YDB_DATABASE
PROCESS_QUEUE_URL
CRON_SECRET
PROCESS_QUEUE_TIMEOUT_SECONDS
```

Use the same service account as the container, with access to YDB.
This function only validates Telegram requests and enqueues updates in YDB.
After enqueueing an update, it can wake the container endpoint
`/telegram/process-queue` when `PROCESS_QUEUE_URL` and `CRON_SECRET` are set.
The wake-up request fails fast and does not make the Telegram webhook fail.

Default:

```text
PROCESS_QUEUE_TIMEOUT_SECONDS=3
```

After deployment, set Telegram webhook to the public function URL:

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=<FUNCTION_URL>&secret_token=<TELEGRAM_WEBHOOK_SECRET>&max_connections=1
```
