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
```

Use the same service account as the container, with access to YDB.

After deployment, set Telegram webhook to the public function URL:

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=<FUNCTION_URL>&secret_token=<TELEGRAM_WEBHOOK_SECRET>&max_connections=1
```
