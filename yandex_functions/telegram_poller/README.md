# Telegram polling Yandex Function

Runtime: Python 3.12

Entrypoint: `index.handler`

Environment variables:

```text
TELEGRAM_BOT_TOKEN
YDB_ENDPOINT
YDB_DATABASE
```

Attach the same service account as the container with the `ydb.editor` role.
Delete the Telegram webhook before enabling the one-minute timer trigger.
