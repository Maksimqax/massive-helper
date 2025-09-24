Развёртывание
=============

1) Заполните `.env` (или переменные окружения на Render):
   - BOT_TOKEN
   - WEBHOOK_URL (ваш публичный URL, например https://massive-helper.onrender.com)
   - SECRET_TOKEN (опционально, но тогда нужен тот же секрет при setWebhook)
   - MAX_FILE_MB (по умолчанию 18)

2) Убедитесь, что на Render включён вебхук (вызывайте GET /set-webhook один раз,
   если не используете авто-настройку). Если включите SECRET_TOKEN — он будет
   сверяться в заголовке X-Telegram-Bot-Api-Secret-Token (иначе 403).

3) Команды GitHub для загрузки:
   git add .
   git commit -m "deploy: full bot with webhook fixes"
   git push

4) Для локального запуска:
   uvicorn app.main:app --reload --port 10000

Примечание: для конвертации нужен ffmpeg в окружении. На Render free его может не быть.
