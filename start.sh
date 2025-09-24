#!/usr/bin/env bash
set -e
# Optional webhook set (idempotent). If SECRET_TOKEN is set, pass it.
if [[ -n "$WEBHOOK_URL" ]]; then
  echo "Setting webhook to $WEBHOOK_URL"
  python - <<'PY'
import os, asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN=os.environ['BOT_TOKEN']
WEBHOOK_URL=os.environ['WEBHOOK_URL']
SECRET_TOKEN=os.getenv('SECRET_TOKEN')

async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    await bot.set_webhook(WEBHOOK_URL, secret_token=SECRET_TOKEN if SECRET_TOKEN else None, drop_pending_updates=True)
    me = await bot.get_me()
    print("Webhook set for", me.username)

asyncio.run(main())
PY
fi

exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}
