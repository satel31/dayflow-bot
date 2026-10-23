# Telegram polling Yandex Function

Runtime: Python 3.12

Entrypoint: `index.handler`

Environment variables:

```text
TELEGRAM_BOT_TOKEN
YDB_ENDPOINT
YDB_DATABASE
PROCESS_QUEUE_URL
CRON_SECRET
TELEGRAM_GET_UPDATES_ATTEMPTS
TELEGRAM_GET_UPDATES_TIMEOUT_SECONDS
PROCESS_QUEUE_TIMEOUT_SECONDS
```

Attach the same service account as the container with the `ydb.editor` role.
Delete the Telegram webhook before enabling the one-minute timer trigger.
Do not enable the timer trigger until network access from the function to
`https://api.telegram.org` is stable.

The poller intentionally fails fast when Telegram is unreachable so a single
timer tick cannot consume the whole Cloud Function timeout. Defaults:

```text
TELEGRAM_GET_UPDATES_ATTEMPTS=1
TELEGRAM_GET_UPDATES_TIMEOUT_SECONDS=2
PROCESS_QUEUE_TIMEOUT_SECONDS=5
```

`PROCESS_QUEUE_URL` is optional. When it is set, the poller calls the container
queue processor only after Telegram returns new updates. Example:

```text
PROCESS_QUEUE_URL=https://<container-host>/telegram/process-queue
```

After verifying this mode, the separate one-minute container trigger can be
disabled to avoid waking the container when the Telegram queue is empty.
