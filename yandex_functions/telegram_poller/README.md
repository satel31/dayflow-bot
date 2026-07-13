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
```

Attach the same service account as the container with the `ydb.editor` role.
Delete the Telegram webhook before enabling the one-minute timer trigger.

`PROCESS_QUEUE_URL` is optional. When it is set, the poller calls the container
queue processor only after Telegram returns new updates. Example:

```text
PROCESS_QUEUE_URL=https://<container-host>/telegram/process-queue
```

After verifying this mode, the separate one-minute container trigger can be
disabled to avoid waking the container when the Telegram queue is empty.
